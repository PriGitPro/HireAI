"""SQLAlchemy ORM models for the Hiring Copilot."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    JSON,
    Enum as SAEnum,
)
from sqlalchemy.orm import relationship

from app.core.database import Base


def _utcnow():
    return datetime.now(timezone.utc)


def _gen_id(prefix: str = "") -> str:
    short = uuid.uuid4().hex[:8].upper()
    return f"{prefix}{short}" if prefix else short


# ── Job Requisition ───────────────────────────────────────────────────────────


class JobRequisition(Base):
    __tablename__ = "job_requisitions"

    id = Column(String, primary_key=True, default=lambda: _gen_id("REQ-"))
    title = Column(String(256), nullable=False)
    department = Column(String(128), nullable=True)
    location = Column(String(128), nullable=True)
    employment_type = Column(String(64), default="Full-time")
    description_raw = Column(Text, nullable=False)
    description_structured = Column(JSON, nullable=True)  # ParsedJobDescription (serialized)
    required_skills = Column(JSON, nullable=True)          # [{name, importance, category}]
    experience_requirements = Column(JSON, nullable=True)
    education_requirements = Column(JSON, nullable=True)
    status = Column(String(32), default="active")          # active | closed | draft
    created_at = Column(DateTime(timezone=True), default=_utcnow)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    # Relationships
    candidates = relationship("Candidate", back_populates="requisition", cascade="all, delete-orphan")


# ── Candidate ─────────────────────────────────────────────────────────────────


class Candidate(Base):
    __tablename__ = "candidates"

    id = Column(String, primary_key=True, default=lambda: _gen_id("CAN-"))
    requisition_id = Column(String, ForeignKey("job_requisitions.id"), nullable=False)
    name = Column(String(256), nullable=False)
    email = Column(String(256), nullable=True)
    phone = Column(String(64), nullable=True)
    resume_filename = Column(String(512), nullable=True)
    resume_path = Column(String(1024), nullable=True)
    resume_text = Column(Text, nullable=True)
    resume_structured = Column(JSON, nullable=True)   # ParsedResume (serialized)
    status = Column(String(32), default="pending")    # pending | evaluated | flagged | hired | rejected
    created_at = Column(DateTime(timezone=True), default=_utcnow)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    # Relationships
    requisition = relationship("JobRequisition", back_populates="candidates")
    evaluation = relationship("Evaluation", back_populates="candidate", uselist=False, cascade="all, delete-orphan")
    audit_logs = relationship("AuditLog", back_populates="candidate", cascade="all, delete-orphan")


# ── Evaluation ────────────────────────────────────────────────────────────────


class Evaluation(Base):
    __tablename__ = "evaluations"

    id = Column(String, primary_key=True, default=lambda: _gen_id("EVL-"))
    candidate_id = Column(String, ForeignKey("candidates.id"), nullable=False, unique=True)

    # ── Decision (deterministic) ──────────────────────────────────────────────
    recommendation = Column(String(32), nullable=False)  # strong_hire | hire | consider | no_hire
    confidence = Column(Float, nullable=False)            # 0.0 – 1.0 (evidence-calibrated)
    composite_score = Column(Float, nullable=True)        # 0 – 100 (weighted signal sum)

    # ── Evaluation signals ────────────────────────────────────────────────────
    # skill_matches: [{skill, match_level, evidence, importance, match_reason, skill_score}]
    skill_matches = Column(JSON, nullable=True)
    experience_assessment = Column(JSON, nullable=True)
    education_assessment = Column(JSON, nullable=True)

    # ── Explainability — signal-derived ──────────────────────────────────────
    # strengths: [{description, evidence, skill}]  — evidence required
    strengths = Column(JSON, nullable=True)
    # gaps: [{skill, severity, description, impact}]  — severity classified
    gaps = Column(JSON, nullable=True)
    explanation = Column(Text, nullable=True)         # signal-derived, not free-form LLM
    # decision_trace: [{step, signal, finding, impact, weight}]
    decision_trace = Column(JSON, nullable=True)
    suggested_actions = Column(JSON, nullable=True)   # derived from gaps + signals

    # ── Human override ────────────────────────────────────────────────────────
    override_decision = Column(String(32), nullable=True)
    override_reason = Column(Text, nullable=True)
    overridden_by = Column(String(256), nullable=True)
    overridden_at = Column(DateTime(timezone=True), nullable=True)

    # ── Observability & audit ─────────────────────────────────────────────────
    # debug_metadata: non-UI structured debug info (scores, weights, evidence density)
    debug_metadata = Column(JSON, nullable=True)
    trace_id = Column(String(32), nullable=True)      # cross-pipeline trace correlation

    # ── Metadata ──────────────────────────────────────────────────────────────
    model_used = Column(String(128), nullable=True)   # "signal-engine" for deterministic stages
    processing_time_ms = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    # Relationships
    candidate = relationship("Candidate", back_populates="evaluation")


# ── Audit Log ─────────────────────────────────────────────────────────────────


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    candidate_id = Column(String, ForeignKey("candidates.id"), nullable=True)
    action = Column(String(64), nullable=False)  # evaluate | override | flag | status_change
    actor = Column(String(256), default="system")
    details = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow)

    # Relationships
    candidate = relationship("Candidate", back_populates="audit_logs")
