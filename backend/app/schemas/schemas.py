"""Pydantic schemas for API request/response validation."""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


# ── Job Requisition ──────────────────────────────────────────────────────────


class SkillRequirement(BaseModel):
    name: str
    importance: str = "important"  # critical | important | secondary
    category: Optional[str] = None


class JobRequisitionCreate(BaseModel):
    title: str = Field(..., min_length=3, max_length=256)
    department: Optional[str] = None
    location: Optional[str] = None
    employment_type: str = "Full-time"
    description_raw: str = Field(..., min_length=20)


class JobRequisitionResponse(BaseModel):
    id: str
    title: str
    department: Optional[str]
    location: Optional[str]
    employment_type: str
    description_raw: str
    description_structured: Optional[dict] = None
    required_skills: Optional[list[dict]] = None
    experience_requirements: Optional[dict] = None
    education_requirements: Optional[dict] = None
    status: str
    created_at: datetime
    updated_at: datetime
    candidate_count: int = 0

    class Config:
        from_attributes = True


class JobRequisitionList(BaseModel):
    items: list[JobRequisitionResponse]
    total: int


# ── Candidate ────────────────────────────────────────────────────────────────


class CandidateCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=256)
    email: Optional[str] = None
    phone: Optional[str] = None


class CandidateResponse(BaseModel):
    id: str
    requisition_id: str
    name: str
    email: Optional[str]
    phone: Optional[str]
    resume_filename: Optional[str]
    status: str
    created_at: datetime
    updated_at: datetime
    has_evaluation: bool = False

    class Config:
        from_attributes = True


class CandidateDetail(CandidateResponse):
    resume_text: Optional[str] = None
    resume_structured: Optional[dict] = None
    evaluation: Optional["EvaluationResponse"] = None


# ── Evaluation ───────────────────────────────────────────────────────────────


class SkillMatch(BaseModel):
    skill: str
    match_level: str  # strong | partial | weak | missing
    evidence: Optional[str] = None
    importance: str = "important"


class EvaluationResponse(BaseModel):
    id: str
    candidate_id: str

    # Decision
    recommendation: str
    confidence: float
    composite_score: Optional[float] = None

    # Signals
    skill_matches: Optional[list[dict]] = None
    experience_assessment: Optional[dict] = None
    education_assessment: Optional[dict] = None
    strengths: Optional[list[str]] = None
    gaps: Optional[list[str]] = None

    # Explainability
    explanation: Optional[str] = None
    decision_trace: Optional[list[dict]] = None

    # Actions
    suggested_actions: Optional[list[str]] = None

    # Override
    override_decision: Optional[str] = None
    override_reason: Optional[str] = None
    overridden_by: Optional[str] = None
    overridden_at: Optional[datetime] = None

    # Metadata
    model_used: Optional[str] = None
    processing_time_ms: Optional[int] = None
    created_at: datetime

    class Config:
        from_attributes = True


class OverrideRequest(BaseModel):
    decision: str = Field(..., pattern="^(strong_hire|hire|maybe|no_hire)$")
    reason: str = Field(..., min_length=10)
    overridden_by: str = Field(default="recruiter")


class EvaluateCandidateRequest(BaseModel):
    """Trigger evaluation for a candidate."""
    force_reevaluate: bool = False


# ── Audit Log ────────────────────────────────────────────────────────────────


class AuditLogResponse(BaseModel):
    id: int
    candidate_id: Optional[str]
    action: str
    actor: str
    details: Optional[dict] = None
    created_at: datetime

    class Config:
        from_attributes = True


# ── Dashboard / Analytics ────────────────────────────────────────────────────


class DashboardStats(BaseModel):
    total_requisitions: int
    active_requisitions: int
    total_candidates: int
    evaluated_candidates: int
    pending_candidates: int
    flagged_candidates: int
    avg_confidence: Optional[float] = None
    recommendation_distribution: dict[str, int] = {}
