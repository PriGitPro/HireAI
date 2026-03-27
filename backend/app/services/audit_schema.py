"""Audit Schema — structured evaluation event records.

Single source of truth for the audit log payload written on every EVALUATE event.

Schema version: resume_eval_v1.5
  v1.0 — initial flat dict
  v1.1 — added trace_id
  v1.2 — added critical_gaps list
  v1.3 — added evidence_density, processing_time_ms, validation fields
  v1.4 — added capability_assessments
  v1.5 — full structured schema: decision, score_breakdown, skill_coverage,
          evidence_quality, processing, validation, system, job/candidate metadata

The details dict stored in AuditLog.details is versioned so downstream consumers
can gate on `schema_version` for backward compatibility.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.models.models import Candidate, JobRequisition
    from app.services.pipeline_schemas import EvaluationOutput, SkillMatchResult

# Increment this when the audit schema changes in a breaking way
ENGINE_VERSION = "resume_eval_v1.5"
SCHEMA_VERSION = "1.5"


def build_evaluation_audit(
    *,
    eval_output: "EvaluationOutput",
    candidate: "Candidate",
    requisition: "JobRequisition",
    processing_time_ms: int,
    stage_times_ms: dict[str, int] | None = None,
    validation_errors: list[str] | None = None,
    evidence_mutations: list[str] | None = None,
    trace_id: str = "",
) -> dict[str, Any]:
    """Build the full structured audit record for an EVALUATE event.

    Returns a JSON-serialisable dict that is stored in AuditLog.details.
    All top-level keys are stable — additions only, never removals.

    Args:
        eval_output:         Final EvaluationOutput from the pipeline.
        candidate:           Candidate ORM object (for metadata).
        requisition:         JobRequisition ORM object (for metadata).
        processing_time_ms:  Total wall-clock time for the pipeline.
        stage_times_ms:      Optional per-stage timing breakdown.
        validation_errors:   Errors from D6 validation.
        evidence_mutations:  Enforcement mutations from D6.
        trace_id:            Pipeline trace correlation ID.
    """
    from app.services.pipeline_schemas import MatchLevel, SkillImportance

    sms = eval_output.skill_matches
    debug = eval_output.debug_metadata or {}

    # ── Skill coverage distribution ───────────────────────────────────────────
    exact    = sum(1 for s in sms if s.match_level == MatchLevel.STRONG)
    semantic = sum(1 for s in sms if s.match_level == MatchLevel.PARTIAL)
    weak     = sum(1 for s in sms if s.match_level == MatchLevel.WEAK)
    missing  = sum(1 for s in sms if s.match_level == MatchLevel.MISSING)

    # ── Critical gaps (canonical names, severity=critical) ────────────────────
    critical_gaps = [
        g.skill for g in eval_output.gaps
        if hasattr(g, "severity") and g.severity.value == "critical"
    ]

    # ── Score breakdown (sourced from debug_metadata set by DecisionAgent) ────
    skills_score      = debug.get("skills_score", 0.0)
    exp_score         = debug.get("exp_score", 0.0)
    edu_score         = debug.get("edu_score", 0.0)
    overall_fit_score = debug.get("overall_fit_score", 0.0)
    weights           = debug.get("weights", {})

    # semantic_relevance = ratio of partial (implied) matches to total evaluated
    semantic_relevance = round(semantic / len(sms), 4) if sms else 0.0

    # ── Evidence quality ──────────────────────────────────────────────────────
    evidence_count = sum(1 for s in sms if s.evidence.strip())

    # ── Capability summary (if computed) ─────────────────────────────────────
    cap_assessments = debug.get("capability_assessments", [])
    capability_summary = {
        "total": len(cap_assessments),
        "strong": sum(1 for c in cap_assessments if c.get("level") == "strong"),
        "partial": sum(1 for c in cap_assessments if c.get("level") == "partial"),
        "weak": sum(1 for c in cap_assessments if c.get("level") == "weak"),
        "missing": sum(1 for c in cap_assessments if c.get("level") == "missing"),
    }

    # ── Processing breakdown ──────────────────────────────────────────────────
    processing: dict[str, Any] = {"total_time_ms": processing_time_ms}
    if stage_times_ms:
        processing.update(stage_times_ms)

    return {
        # ── Envelope ─────────────────────────────────────────────────────────
        "event": "EVALUATE",
        "schema_version": SCHEMA_VERSION,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "trace_id": trace_id,

        # ── Subject metadata (for reproducibility / audit compliance) ─────────
        "candidate": {
            "id": candidate.id,
            "name": candidate.name,
            "email": candidate.email or "",
            "resume_filename": candidate.resume_filename or "",
        },
        "job": {
            "id": requisition.id,
            "title": requisition.title,
            "department": requisition.department or "",
            "location": requisition.location or "",
            "employment_type": requisition.employment_type or "",
        },

        # ── Decision ─────────────────────────────────────────────────────────
        "decision": {
            "recommendation": eval_output.recommendation.value,
            "confidence": round(eval_output.confidence, 4),
            "composite_score": round(eval_output.composite_score, 2),
        },

        # ── Score breakdown (each weighted component) ──────────────────────
        "score_breakdown": {
            "technical_match":   round(skills_score / 100, 4),
            "experience_fit":    round(exp_score / 100, 4),
            "academic_alignment": round(edu_score / 100, 4),
            "semantic_relevance": semantic_relevance,
            "overall_fit":       round(overall_fit_score / 100, 4),
            # Weights used (logged for reproducibility)
            "weights": {
                "skills":      weights.get("skills", 0.40),
                "experience":  weights.get("experience", 0.30),
                "education":   weights.get("education", 0.15),
                "overall_fit": weights.get("overall_fit", 0.15),
            },
            # Thresholds used (logged so regressions are detectable)
            "thresholds": debug.get("thresholds", {
                "strong_hire": 78.0,
                "hire": 62.0,
                "consider": 42.0,
            }),
        },

        # ── Skill coverage distribution ───────────────────────────────────────
        "skill_coverage": {
            "total":    len(sms),
            "exact":    exact,
            "semantic": semantic,
            "weak":     weak,
            "missing":  missing,
            "match_rate": round((exact + semantic) / len(sms), 4) if sms else 0.0,
        },

        # ── Capability layer summary ──────────────────────────────────────────
        "capability_coverage": capability_summary,

        # ── Critical gaps (list of canonical skill names) ─────────────────────
        "critical_gaps": critical_gaps,

        # ── Evidence quality ──────────────────────────────────────────────────
        "evidence_quality": {
            "evidence_density": round(eval_output.evidence_density, 4),
            "signal_consistency": round(eval_output.signal_consistency, 4),
            "gap_severity_score": round(eval_output.gap_severity_score, 4),
            "evidence_count":  evidence_count,
            "skills_evaluated": len(sms),
            "resume_has_evidence": getattr(eval_output, "_resume_has_evidence", None),
        },

        # ── Processing metrics ────────────────────────────────────────────────
        "processing": processing,

        # ── Validation results ────────────────────────────────────────────────
        "validation": {
            "errors": validation_errors or [],
            "evidence_mutations": evidence_mutations or [],
            "passed": not bool(validation_errors),
        },

        # ── System / engine versioning (for reproducibility) ─────────────────
        "system": {
            "engine_version": ENGINE_VERSION,
            "schema_version": SCHEMA_VERSION,
            "model_name": None,  # filled by caller
        },
    }
