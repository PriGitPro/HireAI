"""Evaluation Validator — evidence & schema enforcement layer.

Validates intermediate and final pipeline outputs before persisting:
  - Rejects or downgrades skill matches without evidence
  - Enforces minimum signal requirements
  - Validates schema compliance
  - Produces structured failure traces for debugging

All mutations return new objects (no in-place modification).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.services.pipeline_schemas import (
    EducationAssessment,
    EvaluationOutput,
    ExperienceAssessment,
    GapEntry,
    GapSeverity,
    MatchLevel,
    ParsedJobDescription,
    ParsedResume,
    Recommendation,
    SkillImportance,
    SkillMatchResult,
    StrengthEntry,
    TraceStep,
)

logger = logging.getLogger("hireai.validator")


# ── Validation result ─────────────────────────────────────────────────────────

@dataclass
class ValidationResult:
    is_valid: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    mutations: list[str] = field(default_factory=list)  # What was changed

    def add_error(self, msg: str):
        self.errors.append(msg)
        self.is_valid = False

    def add_warning(self, msg: str):
        self.warnings.append(msg)

    def add_mutation(self, msg: str):
        self.mutations.append(msg)


# ── Parsed JD Validation ──────────────────────────────────────────────────────

def validate_parsed_jd(jd: ParsedJobDescription) -> ValidationResult:
    """Validate parsed job description has sufficient structure."""
    result = ValidationResult()

    if not jd.title or jd.title in ("Unknown Role", ""):
        result.add_warning("JD title could not be extracted — using fallback")

    if not jd.required_skills:
        result.add_error("No required skills extracted from JD — cannot perform matching")

    if not any(s.importance == SkillImportance.CRITICAL for s in jd.required_skills):
        result.add_warning(
            "No critical skills identified in JD — "
            "all skills will be treated as 'important'"
        )

    # Check for canonical name population
    unresolved = [s for s in jd.required_skills if not s.canonical_name.strip()]
    if unresolved:
        result.add_error(f"{len(unresolved)} skills have no canonical name")

    return result


# ── Parsed Resume Validation ──────────────────────────────────────────────────

def validate_parsed_resume(resume: ParsedResume) -> ValidationResult:
    """Validate parsed resume has usable signal."""
    result = ValidationResult()

    if not resume.skills:
        result.add_warning(
            "No skills extracted from resume — matching will produce all-missing results"
        )

    skills_with_evidence = sum(1 for s in resume.skills if s.evidence.strip())
    if resume.skills and skills_with_evidence == 0:
        result.add_warning(
            "No skills have supporting evidence — all match levels will be downgraded"
        )

    if resume.total_experience_years is None:
        result.add_warning("Total experience years not extractable — experience assessment will be limited")

    if not resume.experience:
        result.add_warning("No experience entries found in resume")

    return result


# ── Skill Match Validation & Enforcement ─────────────────────────────────────

def enforce_evidence_guarantees(
    skill_matches: list[SkillMatchResult],
) -> tuple[list[SkillMatchResult], ValidationResult]:
    """Enforce evidence guarantees on skill matches.

    Rules:
      - STRONG/PARTIAL match with no evidence → downgrade to WEAK
      - WEAK match with no evidence → allowed (weak is already uncertain)
      - MISSING → no evidence needed (by definition)

    Returns (validated_matches, result) where result logs all mutations.
    """
    validation = ValidationResult()
    validated: list[SkillMatchResult] = []

    for sm in skill_matches:
        if sm.match_level in (MatchLevel.STRONG, MatchLevel.PARTIAL) and not sm.evidence.strip():
            # Downgrade
            new_sm = sm.model_copy(update={
                "match_level": MatchLevel.WEAK,
                "match_reason": f"[evidence-enforced downgrade] {sm.match_reason}",
                "skill_score": min(sm.skill_score, 0.25),
            })
            validation.add_mutation(
                f"Downgraded {sm.required_skill} from {sm.match_level.value} to weak: no evidence"
            )
            validated.append(new_sm)
            logger.debug(
                f"VALIDATOR | Downgraded {sm.required_skill}: "
                f"{sm.match_level.value} → weak (no evidence)"
            )
        else:
            validated.append(sm)

    if validation.mutations:
        logger.warning(
            f"VALIDATOR | {len(validation.mutations)} evidence-guarantee mutations applied"
        )

    return validated, validation


# ── Final Output Validation ───────────────────────────────────────────────────

def validate_evaluation_output(
    output: EvaluationOutput,
    jd: ParsedJobDescription,
    resume: ParsedResume,
) -> ValidationResult:
    """Validate the final EvaluationOutput before persisting.

    Checks:
      - All required skill matches are present
      - Confidence is calibrated (not arbitrary)
      - Decision trace is ordered and non-empty
      - No hardcoded/generic fallback text leaking through
    """
    result = ValidationResult()

    # ── Check completeness ────────────────────────────────────────────────────
    required_skills = {s.canonical_name for s in jd.required_skills}
    matched_skills = {sm.required_skill for sm in output.skill_matches}
    missing_from_output = required_skills - matched_skills
    if missing_from_output:
        result.add_error(
            f"Evaluation output missing matches for: {', '.join(missing_from_output)}"
        )

    # ── Decision trace ────────────────────────────────────────────────────────
    if not output.decision_trace:
        result.add_error("Decision trace is empty — recommendation is not explainable")

    trace_steps = [t.step for t in output.decision_trace]
    if trace_steps != sorted(trace_steps):
        result.add_warning("Decision trace steps are not in order")

    # ── Evidence guarantee on output ─────────────────────────────────────────
    strong_without_evidence = [
        sm for sm in output.skill_matches
        if sm.match_level == MatchLevel.STRONG and not sm.evidence.strip()
    ]
    if strong_without_evidence:
        names = ", ".join(sm.required_skill for sm in strong_without_evidence)
        result.add_error(f"Strong matches without evidence in final output: {names}")

    # ── Confidence range ──────────────────────────────────────────────────────
    if not (0.0 <= output.confidence <= 1.0):
        result.add_error(f"Confidence {output.confidence} out of range [0, 1]")

    # ── Composite score range ─────────────────────────────────────────────────
    if not (0.0 <= output.composite_score <= 100.0):
        result.add_error(f"Composite score {output.composite_score} out of range [0, 100]")

    # ── Explanation ───────────────────────────────────────────────────────────
    if not output.explanation.strip():
        result.add_warning("Explanation is empty")

    if output.explanation in (
        "The automated evaluation could not complete successfully.",
        "The automated evaluation could not complete successfully. A manual review is strongly recommended.",
    ):
        result.add_warning("Generic fallback explanation detected in final output")

    # ── Strengths have evidence ───────────────────────────────────────────────
    strengths_without_evidence = [s for s in output.strengths if not s.evidence.strip()]
    if strengths_without_evidence:
        result.add_warning(
            f"{len(strengths_without_evidence)} strength(s) lack evidence — consider removing"
        )

    # ── Critical gap consistency ──────────────────────────────────────────────
    # If recommendation is strong_hire/hire but there are critical gaps: warn
    if output.recommendation.value in ("strong_hire", "hire") and output.has_critical_gaps:
        critical_names = ", ".join(g.skill for g in output.critical_gaps)
        result.add_warning(
            f"Positive recommendation despite critical gaps ({critical_names}) — verify decision logic"
        )

    if result.errors:
        logger.error(f"VALIDATOR | Final output INVALID: {result.errors}")
    elif result.warnings:
        logger.warning(f"VALIDATOR | Final output warnings: {result.warnings}")
    else:
        logger.info("VALIDATOR | Final output passed all checks")

    return result


def build_partial_fallback(
    jd: ParsedJobDescription,
    resume: ParsedResume,
    failure_reason: str,
    trace_id: str = "",
) -> EvaluationOutput:
    """Build a partial deterministic output when pipeline fails.

    Unlike the old generic fallback:
    - All required skills are present as MISSING (deterministic)
    - Confidence is low (0.15) with explicit reason
    - Failure is traceable via debug_metadata
    - No hardcoded generic strings
    """
    # All skills as missing
    skill_matches = [
        SkillMatchResult(
            required_skill=s.canonical_name,
            importance=s.importance,
            match_level=MatchLevel.MISSING,
            evidence="",
            match_reason=f"Pipeline failure: {failure_reason}",
            skill_score=0.0,
        )
        for s in jd.required_skills
    ]

    gaps = [
        GapEntry(
            skill=s.canonical_name,
            severity=(
                GapSeverity.CRITICAL
                if s.importance == SkillImportance.CRITICAL
                else GapSeverity.IMPORTANT
            ),
            description=f"Could not assess {s.canonical_name} — pipeline error",
            impact="Unknown — manual review required",
        )
        for s in jd.required_skills[:5]  # Cap to avoid noise
    ]

    trace = [
        TraceStep(
            step=1,
            signal="pipeline_status",
            finding=f"Pipeline failed: {failure_reason}",
            impact="negative",
        )
    ]

    return EvaluationOutput(
        recommendation=Recommendation.MAYBE,
        confidence=0.15,
        composite_score=0.0,
        skill_matches=skill_matches,
        experience_assessment=ExperienceAssessment(score=0.0),
        education_assessment=EducationAssessment(score=0.0),
        strengths=[],
        gaps=gaps,
        explanation=(
            f"Automated evaluation incomplete due to pipeline error: {failure_reason}. "
            f"Manual review is required. All skill assessments are provisional."
        ),
        decision_trace=trace,
        suggested_actions=[
            "Perform manual review — automated pipeline did not complete",
            f"Diagnose pipeline failure: {failure_reason[:100]}",
        ],
        evidence_density=0.0,
        signal_consistency=0.0,
        gap_severity_score=0.0,
        debug_metadata={
            "failure_reason": failure_reason,
            "is_partial_fallback": True,
            "skills_assessed": 0,
            "trace_id": trace_id,
        },
        trace_id=trace_id,
    )
