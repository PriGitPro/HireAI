"""Evaluation Service — signal-driven pipeline orchestrator.

Pipeline stages:
  D1: Load candidate + requisition from DB
  D2: Parse job description → ParsedJobDescription  (LLM-assisted, validated)
  D3: Parse resume → ParsedResume                   (LLM-assisted, validated)
  D4: Matching Engine → deterministic skill/exp/edu signals
  D5: Decision Agent → rule-based recommendation, confidence, trace
  D6: Validate → evidence guarantees, schema compliance
  D7: Persist → store results, write audit log

Key design invariants:
  - LLM is ONLY used for parsing (D2/D3), not for decision-making
  - All decisions are derived from structured signals (D4/D5)
  - Evidence guarantee enforced before persist (D6)
  - Every field in EvaluationOutput is traceable to input signals
  - Re-evaluation with same inputs produces identical results
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import AsyncGenerator, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.models import AuditLog, Candidate, Evaluation, JobRequisition
from app.services.decision_agent import DecisionAgent
from app.services.evaluation_validator import (
    build_partial_fallback,
    enforce_evidence_guarantees,
    validate_evaluation_output,
    validate_parsed_jd,
    validate_parsed_resume,
)
from app.services.llm_provider import LLMResponse, get_llm_provider
from app.services.matching_engine import (
    assess_education,
    assess_experience,
    build_gaps,
    build_strengths,
    build_suggested_actions,
    match_skills,
)
from app.services.ontology import canonicalize, get_parent_category
from app.services.pipeline_schemas import (
    EducationAssessment,
    ExperienceAssessment,
    ParsedEducationEntry,
    ParsedEducationReq,
    ParsedExperienceEntry,
    ParsedExperienceReq,
    ParsedJobDescription,
    ParsedResume,
    ParsedSkillEntry,
    ParsedSkillRequirement,
    SkillCategory,
    SkillImportance,
)
from app.services.prompts import (
    JD_PARSING_PROMPT,
    RESUME_PARSING_PROMPT,
    SYSTEM_PROMPT,
)

logger = logging.getLogger("hireai.evaluation")

# ── In-process cache (parsed JDs and resumes are expensive LLM calls) ─────────
# Keyed by (content_hash,) — avoids redundant LLM calls for same input.
_JD_CACHE: dict[str, ParsedJobDescription] = {}
_RESUME_CACHE: dict[str, ParsedResume] = {}

# Max LLM retries
_LLM_MAX_RETRIES = 2
_LLM_RETRY_DELAY_S = 1.5


def _sse_event(event: str, **data) -> dict:
    """Helper to build a consistent SSE event dict."""
    return {"event": event, "data": data}


def _content_hash(text: str) -> str:
    """Short hash for caching."""
    import hashlib
    return hashlib.md5(text.encode()).hexdigest()[:16]


class EvaluationService:
    """Orchestrates the signal-driven candidate evaluation pipeline."""

    def __init__(self):
        self.llm = get_llm_provider()
        self._decision_agent = DecisionAgent()
        logger.debug("EvaluationService instantiated (signal-driven mode)")

    # ── Public API ────────────────────────────────────────────────────────────

    async def parse_job_description(self, jd_text: str) -> ParsedJobDescription:
        """Parse a raw JD → validated ParsedJobDescription.

        Uses LLM for extraction, then:
          - Canonicalizes all skill names via ontology
          - Validates the output
          - Caches the result (avoids redundant LLM calls)
        """
        cache_key = _content_hash(jd_text)
        if cache_key in _JD_CACHE:
            logger.info("PIPELINE.jd_parse | Cache HIT — skipping LLM call")
            return _JD_CACHE[cache_key]

        logger.info(f"PIPELINE.jd_parse | START | jd_length={len(jd_text)} chars")
        start = time.time()

        raw = await self._llm_call_with_retry(
            prompt=JD_PARSING_PROMPT.format(job_description=jd_text),
            stage="jd_parse",
        )

        parsed = self._build_parsed_jd(raw or {}, jd_text)
        validation = validate_parsed_jd(parsed)

        if validation.errors:
            logger.warning(f"PIPELINE.jd_parse | Validation errors: {validation.errors}")
        if validation.warnings:
            logger.debug(f"PIPELINE.jd_parse | Warnings: {validation.warnings}")

        ms = int((time.time() - start) * 1000)
        logger.info(
            f"PIPELINE.jd_parse | DONE"
            f" | title=\"{parsed.title}\""
            f" | skills={len(parsed.required_skills)}"
            f" | critical={len(parsed.critical_skills)}"
            f" | {ms}ms"
        )

        _JD_CACHE[cache_key] = parsed
        return parsed

    async def parse_resume(self, resume_text: str) -> ParsedResume:
        """Parse a raw resume → validated ParsedResume.

        Uses LLM for extraction, then:
          - Canonicalizes all skill names via ontology
          - Validates the output
          - Caches the result
        """
        cache_key = _content_hash(resume_text)
        if cache_key in _RESUME_CACHE:
            logger.info("PIPELINE.resume_parse | Cache HIT — skipping LLM call")
            return _RESUME_CACHE[cache_key]

        logger.info(f"PIPELINE.resume_parse | START | resume_length={len(resume_text)} chars")
        start = time.time()

        raw = await self._llm_call_with_retry(
            prompt=RESUME_PARSING_PROMPT.format(resume_text=resume_text),
            stage="resume_parse",
        )

        parsed = self._build_parsed_resume(raw or {}, resume_text)
        validation = validate_parsed_resume(parsed)

        if validation.errors:
            logger.warning(f"PIPELINE.resume_parse | Validation errors: {validation.errors}")
        if validation.warnings:
            logger.debug(f"PIPELINE.resume_parse | Warnings: {validation.warnings}")

        ms = int((time.time() - start) * 1000)
        logger.info(
            f"PIPELINE.resume_parse | DONE"
            f" | name=\"{parsed.name}\""
            f" | skills={len(parsed.skills)}"
            f" | skills_with_evidence={sum(1 for s in parsed.skills if s.evidence.strip())}"
            f" | exp_years={parsed.total_experience_years}"
            f" | {ms}ms"
        )

        _RESUME_CACHE[cache_key] = parsed
        return parsed

    # ── SSE Streaming Evaluation ──────────────────────────────────────────────

    async def evaluate_candidate_streaming(
        self,
        db: AsyncSession,
        candidate_id: str,
        force_reevaluate: bool = False,
        trace_id: Optional[str] = None,
    ) -> AsyncGenerator[dict, None]:
        """Run the full evaluation pipeline, yielding SSE events at each stage.

        Stage map:
          stage=loading       → D1 loading
          stage=loaded        → D1 complete
          stage=jd_parsing    → D2 start
          stage=jd_parsed     → D2 complete
          stage=resume_parsing → D3 start
          stage=resume_parsed  → D3 complete
          stage=matching      → D4 (deterministic matching)
          stage=matched       → D4 complete
          stage=deciding      → D5 (rule-based decision)
          stage=saving        → D7 (persist)
          event=cached        → returning cached evaluation
          event=result        → final result
          event=error         → pipeline error
          event=done          → stream complete
        """
        trace_id = trace_id or uuid.uuid4().hex[:12]
        pipeline_start = time.time()

        logger.info("=" * 60)
        logger.info(
            f"PIPELINE | ▶ STREAMING EVALUATION"
            f" | candidate_id={candidate_id}"
            f" | trace_id={trace_id}"
            f" | force={force_reevaluate}"
        )
        logger.info("=" * 60)

        try:
            # ── D1: Load ──────────────────────────────────────────────────────
            yield _sse_event("stage",
                stage="loading", step=1, total_steps=7,
                message="Loading candidate and job requisition...",
                trace_id=trace_id,
            )

            stage_start = time.time()
            candidate = await db.get(Candidate, candidate_id)
            if not candidate:
                yield _sse_event("error", message=f"Candidate {candidate_id} not found", trace_id=trace_id)
                return

            requisition = await db.get(JobRequisition, candidate.requisition_id)
            if not requisition:
                yield _sse_event("error", message=f"Requisition not found", trace_id=trace_id)
                return

            logger.info(
                f"PIPELINE | D1 loaded"
                f" | candidate=\"{candidate.name}\""
                f" | requisition=\"{requisition.title}\""
                f" | {int((time.time() - stage_start) * 1000)}ms"
            )

            yield _sse_event("stage",
                stage="loaded", step=1, total_steps=7,
                message=f"Loaded: {candidate.name} × {requisition.title}",
                candidate_name=candidate.name,
                requisition_title=requisition.title,
                trace_id=trace_id,
            )

            # ── Check cache ───────────────────────────────────────────────────
            if not force_reevaluate:
                existing = await db.execute(
                    select(Evaluation).where(Evaluation.candidate_id == candidate_id)
                )
                existing_eval = existing.scalar_one_or_none()
                if existing_eval:
                    logger.info(f"PIPELINE | Returning CACHED evaluation | trace_id={trace_id}")
                    yield _sse_event("cached",
                        message="Returning existing evaluation",
                        evaluation=self._evaluation_to_dict(existing_eval),
                        trace_id=trace_id,
                    )
                    yield _sse_event("done",
                        total_time_ms=int((time.time() - pipeline_start) * 1000),
                        trace_id=trace_id,
                    )
                    return

            # ── D2: Parse JD ──────────────────────────────────────────────────
            if not requisition.description_structured or force_reevaluate:
                yield _sse_event("stage",
                    stage="jd_parsing", step=2, total_steps=7,
                    message="Parsing job description...",
                    trace_id=trace_id,
                )
                stage_start = time.time()
                jd_parsed = await self.parse_job_description(requisition.description_raw)

                # Persist structured JD back to requisition
                requisition.description_structured = json.loads(
                    jd_parsed.model_dump_json()
                )
                requisition.required_skills = [
                    {"name": s.canonical_name, "importance": s.importance.value, "category": s.category.value}
                    for s in jd_parsed.required_skills
                ]
                requisition.experience_requirements = jd_parsed.experience_requirements.model_dump()
                requisition.education_requirements = jd_parsed.education_requirements.model_dump()
                db.add(requisition)

                stage_ms = int((time.time() - stage_start) * 1000)
                yield _sse_event("stage",
                    stage="jd_parsed", step=2, total_steps=7,
                    message=f"JD parsed — {len(jd_parsed.required_skills)} skills identified",
                    skills_count=len(jd_parsed.required_skills),
                    critical_count=len(jd_parsed.critical_skills),
                    duration_ms=stage_ms,
                    trace_id=trace_id,
                )
            else:
                # Reconstruct from cached DB data
                jd_parsed = self._reconstruct_parsed_jd(requisition)
                yield _sse_event("stage",
                    stage="jd_parsed", step=2, total_steps=7,
                    message=f"JD already parsed — {len(jd_parsed.required_skills)} skills cached",
                    skills_count=len(jd_parsed.required_skills),
                    cached=True, trace_id=trace_id,
                )

            # ── D3: Parse Resume ──────────────────────────────────────────────
            if not candidate.resume_text:
                yield _sse_event("error",
                    message="No resume text available. Upload a resume first.",
                    trace_id=trace_id,
                )
                return

            if not candidate.resume_structured or force_reevaluate:
                yield _sse_event("stage",
                    stage="resume_parsing", step=3, total_steps=7,
                    message="Analyzing resume...",
                    trace_id=trace_id,
                )
                stage_start = time.time()
                resume_parsed = await self.parse_resume(candidate.resume_text)

                # Persist structured resume
                candidate.resume_structured = json.loads(resume_parsed.model_dump_json())
                if not candidate.email and resume_parsed.email:
                    candidate.email = resume_parsed.email
                if not candidate.phone and resume_parsed.phone:
                    candidate.phone = resume_parsed.phone
                db.add(candidate)

                stage_ms = int((time.time() - stage_start) * 1000)
                yield _sse_event("stage",
                    stage="resume_parsed", step=3, total_steps=7,
                    message=f"Resume analyzed — {len(resume_parsed.skills)} skills, {len(resume_parsed.experience)} roles",
                    skills_count=len(resume_parsed.skills),
                    experience_count=len(resume_parsed.experience),
                    skills_with_evidence=sum(1 for s in resume_parsed.skills if s.evidence.strip()),
                    duration_ms=stage_ms,
                    trace_id=trace_id,
                )
            else:
                resume_parsed = self._reconstruct_parsed_resume(candidate)
                yield _sse_event("stage",
                    stage="resume_parsed", step=3, total_steps=7,
                    message=f"Resume already analyzed — {len(resume_parsed.skills)} skills cached",
                    skills_count=len(resume_parsed.skills),
                    cached=True, trace_id=trace_id,
                )

            # ── D4: Deterministic Matching ────────────────────────────────────
            yield _sse_event("stage",
                stage="matching", step=4, total_steps=7,
                message="Running deterministic skill matching...",
                trace_id=trace_id,
            )
            stage_start = time.time()

            skill_matches_raw = match_skills(jd_parsed, resume_parsed)
            skill_matches, enforce_result = enforce_evidence_guarantees(skill_matches_raw)
            experience = assess_experience(jd_parsed, resume_parsed)
            education = assess_education(jd_parsed, resume_parsed)
            gaps = build_gaps(skill_matches, experience)
            strengths = build_strengths(skill_matches, experience, resume_parsed)

            stage_ms = int((time.time() - stage_start) * 1000)
            strong_count = sum(1 for sm in skill_matches if sm.match_level.value == "strong")
            missing_count = sum(1 for sm in skill_matches if sm.match_level.value == "missing")
            critical_missing = sum(
                1 for sm in skill_matches
                if sm.match_level.value == "missing" and sm.importance.value == "critical"
            )

            logger.info(
                f"PIPELINE | D4 matching done"
                f" | strong={strong_count}/{len(skill_matches)}"
                f" | missing={missing_count}"
                f" | critical_missing={critical_missing}"
                f" | evidence_mutations={len(enforce_result.mutations)}"
                f" | {stage_ms}ms"
            )

            yield _sse_event("stage",
                stage="matched", step=4, total_steps=7,
                message=f"Matched {strong_count}/{len(skill_matches)} skills strongly, {missing_count} missing",
                strong_count=strong_count,
                missing_count=missing_count,
                critical_missing=critical_missing,
                gaps_count=len(gaps),
                duration_ms=stage_ms,
                trace_id=trace_id,
            )

            # ── D5: Decision Agent ────────────────────────────────────────────
            yield _sse_event("stage",
                stage="deciding", step=5, total_steps=7,
                message="Applying decision rules...",
                trace_id=trace_id,
            )
            stage_start = time.time()

            suggested_actions = build_suggested_actions(gaps, skill_matches, "")  # placeholder rec
            eval_output = self._decision_agent.decide(
                jd=jd_parsed,
                resume=resume_parsed,
                skill_matches=skill_matches,
                experience=experience,
                education=education,
                gaps=gaps,
                strengths=strengths,
                suggested_actions=suggested_actions,
                trace_id=trace_id,
            )
            # Re-derive actions now that we have the recommendation
            eval_output.suggested_actions = build_suggested_actions(
                gaps, skill_matches, eval_output.recommendation.value
            )

            stage_ms = int((time.time() - stage_start) * 1000)
            logger.info(
                f"PIPELINE | D5 decision"
                f" | recommendation={eval_output.recommendation.value}"
                f" | confidence={eval_output.confidence:.3f}"
                f" | score={eval_output.composite_score}"
                f" | {stage_ms}ms"
            )

            yield _sse_event("stage",
                stage="decided", step=5, total_steps=7,
                message=f"Decision: {eval_output.recommendation.value} (score {eval_output.composite_score:.0f}, confidence {eval_output.confidence:.0%})",
                recommendation=eval_output.recommendation.value,
                confidence=eval_output.confidence,
                composite_score=eval_output.composite_score,
                duration_ms=stage_ms,
                trace_id=trace_id,
            )

            # ── D6: Validate ──────────────────────────────────────────────────
            yield _sse_event("stage",
                stage="validating", step=6, total_steps=7,
                message="Validating evaluation output...",
                trace_id=trace_id,
            )

            final_validation = validate_evaluation_output(eval_output, jd_parsed, resume_parsed)
            if final_validation.errors:
                logger.error(f"PIPELINE | D6 validation FAILED: {final_validation.errors}")
                # Use partial fallback but preserve what we have
                yield _sse_event("stage",
                    stage="validation_warning", step=6, total_steps=7,
                    message=f"Validation warnings — proceeding with reduced confidence",
                    errors=final_validation.errors,
                    warnings=final_validation.warnings,
                    trace_id=trace_id,
                )
                # Penalize confidence for validation failures
                eval_output.confidence = max(0.05, eval_output.confidence * 0.7)
            else:
                yield _sse_event("stage",
                    stage="validated", step=6, total_steps=7,
                    message="Validation passed",
                    mutations=len(enforce_result.mutations),
                    trace_id=trace_id,
                )

            # ── D7: Persist ───────────────────────────────────────────────────
            processing_time = int((time.time() - pipeline_start) * 1000)
            yield _sse_event("stage",
                stage="saving", step=7, total_steps=7,
                message="Saving evaluation results...",
                trace_id=trace_id,
            )
            stage_start = time.time()

            # Remove existing evaluation if re-evaluating
            existing_q = await db.execute(
                select(Evaluation).where(Evaluation.candidate_id == candidate_id)
            )
            existing_eval = existing_q.scalar_one_or_none()
            if existing_eval:
                await db.delete(existing_eval)
                await db.flush()

            db_dict = eval_output.to_db_dict()

            evaluation = Evaluation(
                candidate_id=candidate_id,
                recommendation=db_dict["recommendation"],
                confidence=eval_output.confidence,
                composite_score=eval_output.composite_score,
                skill_matches=db_dict["skill_matches"],
                experience_assessment=db_dict["experience_assessment"],
                education_assessment=db_dict["education_assessment"],
                strengths=db_dict["strengths"],
                gaps=db_dict["gaps"],
                explanation=eval_output.explanation,
                decision_trace=db_dict["decision_trace"],
                suggested_actions=eval_output.suggested_actions,
                debug_metadata=db_dict["debug_metadata"],
                trace_id=trace_id,
                model_used=getattr(self.llm, "model", None) or "signal-engine",
                processing_time_ms=processing_time,
            )
            db.add(evaluation)

            # Update candidate status
            if eval_output.confidence < settings.LOW_CONFIDENCE_THRESHOLD:
                candidate.status = "flagged"
            else:
                candidate.status = "evaluated"
            db.add(candidate)

            # Audit log
            audit = AuditLog(
                candidate_id=candidate_id,
                action="evaluate",
                actor="system",
                details={
                    "recommendation": db_dict["recommendation"],
                    "confidence": eval_output.confidence,
                    "composite_score": eval_output.composite_score,
                    "trace_id": trace_id,
                    "critical_gaps": [g["skill"] for g in db_dict["gaps"] if g["severity"] == "critical"],
                    "evidence_density": eval_output.evidence_density,
                    "processing_time_ms": processing_time,
                    "validation_errors": final_validation.errors,
                    "evidence_mutations": enforce_result.mutations,
                },
            )
            db.add(audit)

            await db.flush()
            await db.refresh(evaluation)

            total_time = int((time.time() - pipeline_start) * 1000)

            logger.info("=" * 60)
            logger.info(
                f"PIPELINE | ✓ COMPLETE"
                f" | candidate=\"{candidate.name}\""
                f" | recommendation={evaluation.recommendation}"
                f" | confidence={evaluation.confidence:.3f}"
                f" | score={evaluation.composite_score}"
                f" | trace_id={trace_id}"
                f" | total={total_time}ms"
            )
            logger.info("=" * 60)

            yield _sse_event("result",
                evaluation=self._evaluation_to_dict(evaluation),
                candidate_status=candidate.status,
                trace_id=trace_id,
            )
            yield _sse_event("done",
                total_time_ms=total_time,
                message=f"Evaluation complete in {total_time / 1000:.1f}s",
                trace_id=trace_id,
            )

        except Exception as e:
            logger.error(f"PIPELINE | FATAL: {type(e).__name__}: {e}", exc_info=True)
            yield _sse_event("error", message=str(e), trace_id=trace_id or "unknown")

    # ── Synchronous evaluate (backward compat) ────────────────────────────────

    async def evaluate_candidate(
        self,
        db: AsyncSession,
        candidate_id: str,
        force_reevaluate: bool = False,
        trace_id: Optional[str] = None,
    ) -> Evaluation:
        """Non-streaming evaluation — collects all events and returns final Evaluation."""
        result = None
        async for event in self.evaluate_candidate_streaming(
            db, candidate_id, force_reevaluate, trace_id
        ):
            if event["event"] in ("result", "cached"):
                eval_q = await db.execute(
                    select(Evaluation).where(Evaluation.candidate_id == candidate_id)
                )
                result = eval_q.scalar_one_or_none()
            elif event["event"] == "error":
                raise ValueError(event["data"]["message"])

        if not result:
            raise ValueError("Evaluation pipeline completed without producing a result")
        return result

    async def override_decision(
        self,
        db: AsyncSession,
        evaluation_id: str,
        decision: str,
        reason: str,
        overridden_by: str = "recruiter",
    ) -> Evaluation:
        """Override an AI-generated decision with a human decision."""
        logger.info(
            f"PIPELINE.override | evaluation_id={evaluation_id}"
            f" | new_decision={decision}"
            f" | overridden_by={overridden_by}"
        )

        evaluation = await db.get(Evaluation, evaluation_id)
        if not evaluation:
            raise ValueError(f"Evaluation {evaluation_id} not found")

        old_decision = evaluation.recommendation
        evaluation.override_decision = decision
        evaluation.override_reason = reason
        evaluation.overridden_by = overridden_by
        evaluation.overridden_at = datetime.now(timezone.utc)
        db.add(evaluation)

        audit = AuditLog(
            candidate_id=evaluation.candidate_id,
            action="override",
            actor=overridden_by,
            details={
                "previous_recommendation": old_decision,
                "new_decision": decision,
                "reason": reason,
                "trace_id": evaluation.trace_id,
            },
        )
        db.add(audit)

        await db.flush()
        await db.refresh(evaluation)
        logger.info(f"PIPELINE.override | {old_decision} → {decision}")
        return evaluation

    # ── Serialization ─────────────────────────────────────────────────────────

    def _evaluation_to_dict(self, e: Evaluation) -> dict:
        """Convert an Evaluation ORM object to a JSON-safe dict for SSE/API."""
        return {
            "id": e.id,
            "candidate_id": e.candidate_id,
            "recommendation": e.recommendation,
            "confidence": e.confidence,
            "composite_score": e.composite_score,
            "skill_matches": e.skill_matches,
            "experience_assessment": e.experience_assessment,
            "education_assessment": e.education_assessment,
            "strengths": e.strengths,
            "gaps": e.gaps,
            "explanation": e.explanation,
            "decision_trace": e.decision_trace,
            "suggested_actions": e.suggested_actions,
            "override_decision": e.override_decision,
            "override_reason": e.override_reason,
            "overridden_by": e.overridden_by,
            "overridden_at": e.overridden_at.isoformat() if e.overridden_at else None,
            "model_used": e.model_used,
            "processing_time_ms": e.processing_time_ms,
            "trace_id": e.trace_id,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        }

    # ── LLM helpers ───────────────────────────────────────────────────────────

    async def _llm_call_with_retry(
        self,
        prompt: str,
        stage: str,
    ) -> Optional[dict]:
        """Call LLM with retry + timeout. Returns parsed JSON or None."""
        last_error = None
        for attempt in range(1, _LLM_MAX_RETRIES + 1):
            try:
                response: LLMResponse = await asyncio.wait_for(
                    self.llm.generate(prompt=prompt, system_prompt=SYSTEM_PROMPT),
                    timeout=settings.LLM_TIMEOUT,
                )
                parsed = response.as_json()
                if parsed:
                    if attempt > 1:
                        logger.info(f"PIPELINE.{stage} | LLM succeeded on attempt {attempt}")
                    return parsed
                else:
                    logger.warning(
                        f"PIPELINE.{stage} | LLM attempt {attempt}: no parseable JSON"
                        f" | latency={response.latency_ms}ms"
                    )
            except asyncio.TimeoutError:
                last_error = f"LLM timeout after {settings.LLM_TIMEOUT}s"
                logger.warning(f"PIPELINE.{stage} | Attempt {attempt}: {last_error}")
            except Exception as e:
                last_error = str(e)
                logger.warning(f"PIPELINE.{stage} | Attempt {attempt}: {type(e).__name__}: {e}")

            if attempt < _LLM_MAX_RETRIES:
                await asyncio.sleep(_LLM_RETRY_DELAY_S)

        logger.error(f"PIPELINE.{stage} | All {_LLM_MAX_RETRIES} LLM attempts failed: {last_error}")
        return None

    # ── Schema builders (LLM JSON → Pydantic) ─────────────────────────────────

    def _build_parsed_jd(self, raw: dict, original_text: str) -> ParsedJobDescription:
        """Convert raw LLM JSON → canonical ParsedJobDescription."""
        skills = []
        for s in raw.get("required_skills", []):
            if not isinstance(s, dict):
                continue
            raw_name = s.get("name", "").strip()
            if not raw_name:
                continue
            canonical = canonicalize(raw_name)
            parent = get_parent_category(canonical)

            importance_raw = s.get("importance", "important").lower()
            importance = {
                "critical": SkillImportance.CRITICAL,
                "important": SkillImportance.IMPORTANT,
                "secondary": SkillImportance.SECONDARY,
            }.get(importance_raw, SkillImportance.IMPORTANT)

            category_raw = s.get("category", "technical").lower()
            category = {
                "technical": SkillCategory.TECHNICAL,
                "soft": SkillCategory.SOFT,
                "domain": SkillCategory.DOMAIN,
            }.get(category_raw, SkillCategory.TECHNICAL)

            skills.append(ParsedSkillRequirement(
                name=raw_name,
                canonical_name=canonical,
                importance=importance,
                category=category,
                parent_category=parent,
            ))

        exp_raw = raw.get("experience_requirements") or {}
        experience_req = ParsedExperienceReq(
            min_years=exp_raw.get("min_years"),
            max_years=exp_raw.get("max_years"),
            preferred_areas=exp_raw.get("preferred_areas") or [],
            description=exp_raw.get("description") or "",
        )

        edu_raw = raw.get("education_requirements") or {}
        education_req = ParsedEducationReq(
            min_level=edu_raw.get("min_level") or "none",
            preferred_fields=edu_raw.get("preferred_fields") or [],
            description=edu_raw.get("description") or "",
        )

        return ParsedJobDescription(
            title=raw.get("title", "Unknown Role") or "Unknown Role",
            summary=raw.get("summary", ""),
            required_skills=skills,
            experience_requirements=experience_req,
            education_requirements=education_req,
            key_responsibilities=raw.get("key_responsibilities", []),
            nice_to_haves=raw.get("nice_to_haves", []),
            parsed_from_llm=True,
            confidence_in_parse=1.0 if skills else 0.3,
        )

    def _build_parsed_resume(self, raw: dict, original_text: str) -> ParsedResume:
        """Convert raw LLM JSON → canonical ParsedResume."""
        skills = []
        for s in raw.get("skills", []):
            if not isinstance(s, dict):
                continue
            raw_name = (s.get("name") or "").strip()
            if not raw_name:
                continue
            canonical = canonicalize(raw_name)
            parent = get_parent_category(canonical)
            evidence = (s.get("evidence") or "").strip()

            skills.append(ParsedSkillEntry(
                name=raw_name,
                canonical_name=canonical,
                proficiency=s.get("proficiency") or "intermediate",
                evidence=evidence,
                parent_category=parent,
            ))

        experience = []
        for e in raw.get("experience", []):
            if not isinstance(e, dict):
                continue
            experience.append(ParsedExperienceEntry(
                title=e.get("title") or "",
                company=e.get("company") or "",
                duration=e.get("duration") or "",
                highlights=[h for h in (e.get("highlights") or []) if isinstance(h, str)],
            ))

        education = []
        for e in raw.get("education", []):
            if not isinstance(e, dict):
                continue
            education.append(ParsedEducationEntry(
                degree=e.get("degree") or "",
                field=e.get("field") or "",
                institution=e.get("institution") or "",
                year=str(e.get("year", "")) if e.get("year") else None,
            ))

        total_years = raw.get("total_experience_years")
        if total_years is not None:
            try:
                total_years = float(total_years)
            except (TypeError, ValueError):
                total_years = None

        return ParsedResume(
            name=raw.get("name", "Unknown") or "Unknown",
            email=raw.get("email"),
            phone=raw.get("phone"),
            summary=raw.get("summary", ""),
            skills=skills,
            experience=experience,
            total_experience_years=total_years,
            education=education,
            certifications=raw.get("certifications", []),
            notable_achievements=raw.get("notable_achievements", []),
            parsed_from_llm=True,
        )

    def _reconstruct_parsed_jd(self, requisition: JobRequisition) -> ParsedJobDescription:
        """Reconstruct ParsedJobDescription from DB-cached structured data."""
        raw = requisition.description_structured or {}
        if not raw:
            # Minimal reconstruction from required_skills
            raw = {
                "title": requisition.title,
                "required_skills": requisition.required_skills or [],
                "experience_requirements": requisition.experience_requirements or {},
                "education_requirements": requisition.education_requirements or {},
            }
        return self._build_parsed_jd(raw, requisition.description_raw or "")

    def _reconstruct_parsed_resume(self, candidate: Candidate) -> ParsedResume:
        """Reconstruct ParsedResume from DB-cached structured data."""
        raw = candidate.resume_structured or {}
        return self._build_parsed_resume(raw, candidate.resume_text or "")
