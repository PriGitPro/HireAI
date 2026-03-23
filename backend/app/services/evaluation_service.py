"""Evaluation Service — orchestrates the full candidate evaluation pipeline.

Pipeline stages (all comprehensively logged):
1. Load candidate + requisition from DB
2. Parse job description → structured requirements  (LLM call)
3. Parse resume → structured candidate profile       (LLM call)
4. Match candidate against requirements → evaluation  (LLM call)
5. Store results, update status, write audit log

Supports two modes:
- Synchronous: evaluate_candidate() returns final Evaluation object
- Streaming (SSE): evaluate_candidate_streaming() yields {event, data} dicts
"""

import json
import logging
import time
from datetime import datetime, timezone
from typing import AsyncGenerator

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.models import (
    AuditLog,
    Candidate,
    Evaluation,
    JobRequisition,
)
from app.services.llm_provider import get_llm_provider, LLMResponse
from app.services.prompts import (
    SYSTEM_PROMPT,
    JD_PARSING_PROMPT,
    RESUME_PARSING_PROMPT,
    EVALUATION_PROMPT,
)

logger = logging.getLogger("hireai.evaluation")


def _sse_event(event: str, **data) -> dict:
    """Helper to build a consistent SSE event dict."""
    return {"event": event, "data": data}


class EvaluationService:
    """Orchestrates the full AI-powered candidate evaluation pipeline."""

    def __init__(self):
        self.llm = get_llm_provider()
        logger.debug("EvaluationService instantiated")

    # ── Public API ───────────────────────────────────────────────────────

    async def parse_job_description(self, jd_text: str) -> dict:
        """Parse a raw job description into structured requirements."""
        logger.info(
            f"PIPELINE.jd_parse | START"
            f" | jd_length={len(jd_text)} chars"
        )
        start = time.time()

        prompt = JD_PARSING_PROMPT.format(job_description=jd_text)
        logger.debug(f"PIPELINE.jd_parse | Prompt assembled | prompt_length={len(prompt)} chars")

        response = await self.llm.generate(prompt=prompt, system_prompt=SYSTEM_PROMPT)
        parsed = response.as_json()

        if not parsed:
            logger.warning(
                f"PIPELINE.jd_parse | LLM returned no parseable JSON — using fallback"
                f" | llm_latency={response.latency_ms}ms"
            )
            parsed = self._fallback_jd_parse(jd_text)
        else:
            skills_count = len(parsed.get("required_skills", []))
            title = parsed.get("title", "?")
            logger.info(
                f"PIPELINE.jd_parse | SUCCESS"
                f" | title=\"{title}\""
                f" | skills_extracted={skills_count}"
                f" | llm_latency={response.latency_ms}ms"
                f" | total_time={int((time.time() - start) * 1000)}ms"
            )

        return parsed

    async def parse_resume(self, resume_text: str) -> dict:
        """Parse a raw resume into a structured candidate profile."""
        logger.info(
            f"PIPELINE.resume_parse | START"
            f" | resume_length={len(resume_text)} chars"
        )
        start = time.time()

        prompt = RESUME_PARSING_PROMPT.format(resume_text=resume_text)
        response = await self.llm.generate(prompt=prompt, system_prompt=SYSTEM_PROMPT)
        parsed = response.as_json()

        if not parsed:
            logger.warning(
                f"PIPELINE.resume_parse | LLM returned no parseable JSON — using fallback"
                f" | llm_latency={response.latency_ms}ms"
            )
            parsed = self._fallback_resume_parse(resume_text)
        else:
            name = parsed.get("name", "?")
            skills_count = len(parsed.get("skills", []))
            exp_count = len(parsed.get("experience", []))
            total_years = parsed.get("total_experience_years", "?")
            logger.info(
                f"PIPELINE.resume_parse | SUCCESS"
                f" | candidate_name=\"{name}\""
                f" | skills_extracted={skills_count}"
                f" | experience_entries={exp_count}"
                f" | total_years={total_years}"
                f" | llm_latency={response.latency_ms}ms"
                f" | total_time={int((time.time() - start) * 1000)}ms"
            )

        return parsed

    # ── SSE Streaming Evaluation ─────────────────────────────────────────

    async def evaluate_candidate_streaming(
        self,
        db: AsyncSession,
        candidate_id: str,
        force_reevaluate: bool = False,
    ) -> AsyncGenerator[dict, None]:
        """Run the full evaluation pipeline, yielding SSE events at each stage.

        Yields dicts of shape: {"event": str, "data": dict}

        Events:
            stage      — Pipeline stage progress update
            cached     — Returning existing evaluation (skipping pipeline)
            result     — Final evaluation result
            error      — Error during evaluation
            done       — Stream complete signal
        """
        pipeline_start = time.time()

        logger.info("=" * 60)
        logger.info(
            f"PIPELINE.stream | ▶▶▶ STARTING STREAMING EVALUATION"
            f" | candidate_id={candidate_id}"
            f" | force_reevaluate={force_reevaluate}"
        )
        logger.info("=" * 60)

        try:
            # ── Stage 1: Load entities ───────────────────────────────
            yield _sse_event("stage",
                stage="loading",
                step=1,
                total_steps=5,
                message="Loading candidate and job requisition...",
            )

            stage_start = time.time()
            candidate = await db.get(Candidate, candidate_id)
            if not candidate:
                yield _sse_event("error", message=f"Candidate {candidate_id} not found")
                return

            requisition = await db.get(JobRequisition, candidate.requisition_id)
            if not requisition:
                yield _sse_event("error", message=f"Requisition {candidate.requisition_id} not found")
                return

            logger.info(
                f"PIPELINE.stream | Stage 1 COMPLETE"
                f" | candidate=\"{candidate.name}\""
                f" | requisition=\"{requisition.title}\""
                f" | {int((time.time() - stage_start) * 1000)}ms"
            )

            yield _sse_event("stage",
                stage="loaded",
                step=1,
                total_steps=5,
                message=f"Loaded: {candidate.name} × {requisition.title}",
                candidate_name=candidate.name,
                requisition_title=requisition.title,
            )

            # Check cache
            if not force_reevaluate:
                existing = await db.execute(
                    select(Evaluation).where(Evaluation.candidate_id == candidate_id)
                )
                existing_eval = existing.scalar_one_or_none()
                if existing_eval:
                    logger.info(f"PIPELINE.stream | RETURNING CACHED evaluation")
                    yield _sse_event("cached",
                        message="Returning existing evaluation",
                        evaluation=self._evaluation_to_dict(existing_eval),
                    )
                    yield _sse_event("done", total_time_ms=int((time.time() - pipeline_start) * 1000))
                    return

            # ── Stage 2: Parse JD ────────────────────────────────────
            if not requisition.description_structured:
                yield _sse_event("stage",
                    stage="jd_parsing",
                    step=2,
                    total_steps=5,
                    message="Parsing job description with AI...",
                )

                stage_start = time.time()
                structured_jd = await self.parse_job_description(requisition.description_raw)
                requisition.description_structured = structured_jd
                requisition.required_skills = structured_jd.get("required_skills", [])
                requisition.experience_requirements = structured_jd.get("experience_requirements")
                requisition.education_requirements = structured_jd.get("education_requirements")
                db.add(requisition)

                skills_count = len(requisition.required_skills or [])
                stage_ms = int((time.time() - stage_start) * 1000)
                logger.info(f"PIPELINE.stream | Stage 2 COMPLETE | skills={skills_count} | {stage_ms}ms")

                yield _sse_event("stage",
                    stage="jd_parsed",
                    step=2,
                    total_steps=5,
                    message=f"Job description parsed — {skills_count} skills identified",
                    skills_count=skills_count,
                    duration_ms=stage_ms,
                )
            else:
                skills_count = len(requisition.required_skills or [])
                yield _sse_event("stage",
                    stage="jd_parsed",
                    step=2,
                    total_steps=5,
                    message=f"Job description already parsed — {skills_count} skills cached",
                    skills_count=skills_count,
                    cached=True,
                )

            # ── Stage 3: Parse Resume ────────────────────────────────
            if not candidate.resume_text:
                yield _sse_event("error", message="No resume text available. Upload a resume first.")
                return

            if not candidate.resume_structured:
                yield _sse_event("stage",
                    stage="resume_parsing",
                    step=3,
                    total_steps=5,
                    message="Analyzing resume with AI...",
                )

                stage_start = time.time()
                structured_resume = await self.parse_resume(candidate.resume_text)
                candidate.resume_structured = structured_resume

                if not candidate.email and structured_resume.get("email"):
                    candidate.email = structured_resume["email"]
                if not candidate.phone and structured_resume.get("phone"):
                    candidate.phone = structured_resume["phone"]
                db.add(candidate)

                resume_skills = len(structured_resume.get("skills", []))
                resume_exp = len(structured_resume.get("experience", []))
                stage_ms = int((time.time() - stage_start) * 1000)
                logger.info(f"PIPELINE.stream | Stage 3 COMPLETE | skills={resume_skills} | {stage_ms}ms")

                yield _sse_event("stage",
                    stage="resume_parsed",
                    step=3,
                    total_steps=5,
                    message=f"Resume analyzed — {resume_skills} skills, {resume_exp} experiences found",
                    skills_count=resume_skills,
                    experience_count=resume_exp,
                    duration_ms=stage_ms,
                )
            else:
                resume_skills = len(candidate.resume_structured.get("skills", []))
                yield _sse_event("stage",
                    stage="resume_parsed",
                    step=3,
                    total_steps=5,
                    message=f"Resume already analyzed — {resume_skills} skills cached",
                    skills_count=resume_skills,
                    cached=True,
                )

            # ── Stage 4: AI Evaluation ───────────────────────────────
            yield _sse_event("stage",
                stage="evaluating",
                step=4,
                total_steps=5,
                message="Running AI evaluation — matching candidate to requirements...",
            )

            stage_start = time.time()
            job_requirements = json.dumps(requisition.description_structured, indent=2)
            candidate_profile = json.dumps(candidate.resume_structured, indent=2)

            prompt = EVALUATION_PROMPT.format(
                job_requirements=job_requirements,
                candidate_profile=candidate_profile,
            )

            eval_response = await self.llm.generate(
                prompt=prompt,
                system_prompt=SYSTEM_PROMPT,
            )
            eval_data = eval_response.as_json()

            if not eval_data:
                logger.error("PIPELINE.stream | Stage 4: LLM returned no parseable JSON — USING FALLBACK")
                eval_data = self._fallback_evaluation()

            recommendation = eval_data.get("recommendation", "?")
            confidence = eval_data.get("confidence", "?")
            composite_score = eval_data.get("composite_score", "?")
            stage_ms = int((time.time() - stage_start) * 1000)

            logger.info(
                f"PIPELINE.stream | Stage 4 COMPLETE"
                f" | recommendation={recommendation}"
                f" | confidence={confidence}"
                f" | score={composite_score}"
                f" | {stage_ms}ms"
            )

            yield _sse_event("stage",
                stage="evaluated",
                step=4,
                total_steps=5,
                message=f"AI evaluation complete — {recommendation} (confidence: {confidence})",
                recommendation=recommendation,
                confidence=confidence,
                composite_score=composite_score,
                duration_ms=stage_ms,
            )

            processing_time = int((time.time() - pipeline_start) * 1000)

            # ── Stage 5: Persist ─────────────────────────────────────
            yield _sse_event("stage",
                stage="saving",
                step=5,
                total_steps=5,
                message="Saving evaluation results...",
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

            evaluation = Evaluation(
                candidate_id=candidate_id,
                recommendation=eval_data.get("recommendation", "maybe"),
                confidence=min(max(eval_data.get("confidence", 0.5), 0.0), 1.0),
                composite_score=eval_data.get("composite_score"),
                skill_matches=eval_data.get("skill_matches"),
                experience_assessment=eval_data.get("experience_assessment"),
                education_assessment=eval_data.get("education_assessment"),
                strengths=eval_data.get("strengths"),
                gaps=eval_data.get("gaps"),
                explanation=eval_data.get("explanation"),
                decision_trace=eval_data.get("decision_trace"),
                suggested_actions=eval_data.get("suggested_actions"),
                model_used=eval_response.model,
                processing_time_ms=processing_time,
            )
            db.add(evaluation)

            # Update candidate status
            conf = evaluation.confidence
            if conf < settings.LOW_CONFIDENCE_THRESHOLD:
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
                    "recommendation": evaluation.recommendation,
                    "confidence": evaluation.confidence,
                    "composite_score": evaluation.composite_score,
                    "model": evaluation.model_used,
                    "processing_time_ms": processing_time,
                },
            )
            db.add(audit)

            await db.flush()
            await db.refresh(evaluation)

            stage_ms = int((time.time() - stage_start) * 1000)
            total_time = int((time.time() - pipeline_start) * 1000)

            logger.info("=" * 60)
            logger.info(
                f"PIPELINE.stream | ◀◀◀ EVALUATION COMPLETE"
                f" | candidate=\"{candidate.name}\""
                f" | recommendation={evaluation.recommendation}"
                f" | confidence={evaluation.confidence:.3f}"
                f" | total_pipeline_time={total_time}ms"
            )
            logger.info("=" * 60)

            # ── Final result event ───────────────────────────────────
            yield _sse_event("result",
                evaluation=self._evaluation_to_dict(evaluation),
                candidate_status=candidate.status,
            )

            yield _sse_event("done",
                total_time_ms=total_time,
                message=f"Evaluation complete in {total_time / 1000:.1f}s",
            )

        except Exception as e:
            logger.error(f"PIPELINE.stream | FATAL ERROR: {type(e).__name__}: {e}", exc_info=True)
            yield _sse_event("error", message=str(e))

    # ── Synchronous evaluate (kept for backward compat) ──────────────────

    async def evaluate_candidate(
        self,
        db: AsyncSession,
        candidate_id: str,
        force_reevaluate: bool = False,
    ) -> Evaluation:
        """Non-streaming evaluation — collects all events and returns final Evaluation."""
        result = None
        async for event in self.evaluate_candidate_streaming(db, candidate_id, force_reevaluate):
            if event["event"] == "result":
                # Need to fetch the actual ORM object
                eval_q = await db.execute(
                    select(Evaluation).where(Evaluation.candidate_id == candidate_id)
                )
                result = eval_q.scalar_one_or_none()
            elif event["event"] == "cached":
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
            f"PIPELINE.override | START"
            f" | evaluation_id={evaluation_id}"
            f" | new_decision={decision}"
            f" | overridden_by={overridden_by}"
        )

        evaluation = await db.get(Evaluation, evaluation_id)
        if not evaluation:
            logger.error(f"PIPELINE.override | Evaluation {evaluation_id} not found")
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
            },
        )
        db.add(audit)

        await db.flush()
        await db.refresh(evaluation)

        logger.info(
            f"PIPELINE.override | COMPLETE"
            f" | candidate_id={evaluation.candidate_id}"
            f" | old={old_decision} → new={decision}"
        )

        return evaluation

    # ── Serialization ────────────────────────────────────────────────────

    def _evaluation_to_dict(self, e: Evaluation) -> dict:
        """Convert an Evaluation ORM object to a JSON-safe dict for SSE."""
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
            "created_at": e.created_at.isoformat() if e.created_at else None,
        }

    # ── Fallbacks ────────────────────────────────────────────────────────

    def _fallback_jd_parse(self, jd_text: str) -> dict:
        logger.warning("PIPELINE.fallback | Using fallback JD parser (no LLM)")
        return {
            "title": "Unknown Role",
            "summary": jd_text[:200],
            "required_skills": [],
            "experience_requirements": {
                "min_years": None, "max_years": None,
                "preferred_areas": [], "description": "",
            },
            "education_requirements": {
                "min_level": "none", "preferred_fields": [], "description": "",
            },
            "key_responsibilities": [],
            "nice_to_haves": [],
        }

    def _fallback_resume_parse(self, resume_text: str) -> dict:
        logger.warning("PIPELINE.fallback | Using fallback resume parser (no LLM)")
        return {
            "name": "Unknown", "email": None, "phone": None,
            "summary": resume_text[:200],
            "skills": [], "experience": [],
            "total_experience_years": None,
            "education": [], "certifications": [],
            "notable_achievements": [],
        }

    def _fallback_evaluation(self) -> dict:
        logger.warning("PIPELINE.fallback | Using fallback evaluation (no LLM)")
        return {
            "recommendation": "maybe",
            "confidence": 0.3,
            "composite_score": 50,
            "skill_matches": [],
            "experience_assessment": {"meets_requirements": False, "score": 50},
            "education_assessment": {"meets_requirements": False, "score": 50},
            "strengths": ["Unable to fully assess — manual review recommended"],
            "gaps": ["Automated evaluation could not complete — review required"],
            "explanation": "The automated evaluation could not complete successfully. A manual review is strongly recommended.",
            "decision_trace": [
                {"step": 1, "action": "Evaluation attempted", "finding": "LLM response could not be parsed", "impact": "negative"}
            ],
            "suggested_actions": ["Perform manual resume review", "Re-run evaluation with a different model"],
        }
