"""Pydantic schemas for all intermediate and final pipeline stages.

These are the internal contracts between pipeline stages — separate from the
public API schemas in app/schemas/schemas.py.

All pipeline stages must produce outputs that conform to these schemas.
The validation layer (evaluation_validator.py) enforces them before persist.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field, field_validator, model_validator


# ── Enums ─────────────────────────────────────────────────────────────────────

class SkillImportance(str, Enum):
    CRITICAL = "critical"
    IMPORTANT = "important"
    SECONDARY = "secondary"


class SkillCategory(str, Enum):
    TECHNICAL = "technical"
    SOFT = "soft"
    DOMAIN = "domain"


class MatchLevel(str, Enum):
    STRONG = "strong"
    PARTIAL = "partial"
    WEAK = "weak"
    MISSING = "missing"


class Recommendation(str, Enum):
    STRONG_HIRE = "strong_hire"
    HIRE = "hire"
    CONSIDER = "consider"
    NO_HIRE = "no_hire"


class GapSeverity(str, Enum):
    CRITICAL = "critical"   # blockers — will trigger no_hire or consider ceiling
    IMPORTANT = "important"  # significant but not blocking
    MINOR = "minor"         # nice-to-have misses


class CapabilityLevel(str, Enum):
    """Aggregate match level for a whole capability area."""
    STRONG  = "strong"   # ≥ 70% of capability skills matched (strong or partial)
    PARTIAL = "partial"  # 40–69%
    WEAK    = "weak"     # 10–39%
    MISSING = "missing"  # < 10%


class YearsMatch(str, Enum):
    EXCEEDS = "exceeds"
    MEETS = "meets"
    BELOW = "below"
    UNKNOWN = "unknown"


class Relevance(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNKNOWN = "unknown"


# ── Stage D2: Parsed Job Description ─────────────────────────────────────────

class ParsedSkillRequirement(BaseModel):
    """A single skill extracted from a JD."""
    name: str = Field(..., min_length=1, description="Raw skill name from JD")
    canonical_name: str = Field(..., min_length=1, description="Ontology-resolved name")
    importance: SkillImportance = SkillImportance.IMPORTANT
    category: SkillCategory = SkillCategory.TECHNICAL
    parent_category: Optional[str] = None  # Set by ontology
    capability_label: Optional[str] = None  # Original JD capability area phrase (e.g. "Agent Architecture & Engineering")

    @field_validator("name", "canonical_name", mode="before")
    @classmethod
    def strip_name(cls, v):
        return str(v).strip() if v else v


class ParsedExperienceReq(BaseModel):
    min_years: Optional[float] = Field(None, ge=0)
    max_years: Optional[float] = Field(None, ge=0)
    preferred_areas: list[str] = Field(default_factory=list)
    description: str = ""


class ParsedEducationReq(BaseModel):
    min_level: str = "none"  # none | bachelor | master | phd
    preferred_fields: list[str] = Field(default_factory=list)
    description: str = ""


class ParsedJobDescription(BaseModel):
    """Output of Stage D2 (JD parsing)."""
    title: str
    summary: str = ""
    required_skills: list[ParsedSkillRequirement] = Field(default_factory=list)
    experience_requirements: ParsedExperienceReq = Field(default_factory=ParsedExperienceReq)
    education_requirements: ParsedEducationReq = Field(default_factory=ParsedEducationReq)
    key_responsibilities: list[str] = Field(default_factory=list)
    nice_to_haves: list[str] = Field(default_factory=list)
    # Metadata
    parsed_from_llm: bool = True
    confidence_in_parse: float = Field(1.0, ge=0.0, le=1.0)

    @property
    def critical_skills(self) -> list[ParsedSkillRequirement]:
        return [s for s in self.required_skills if s.importance == SkillImportance.CRITICAL]

    @property
    def important_skills(self) -> list[ParsedSkillRequirement]:
        return [s for s in self.required_skills if s.importance == SkillImportance.IMPORTANT]


# ── Capability Assessment (D4c) ───────────────────────────────────────────────

class CapabilityAssessment(BaseModel):
    """Aggregate evaluation for a single capability area from the JD.

    A capability is a named group of required skills (e.g. 'Agent Architecture
    & Engineering').  This is computed deterministically from the SkillMatchResults
    — no additional LLM call required.
    """
    capability: str            # e.g. "Agent Architecture & Engineering"
    level: CapabilityLevel
    score: float = Field(..., ge=0.0, le=100.0)  # 0–100 aggregate
    total_skills: int          # number of JD skills in this capability group
    matched_skills: int        # strong + partial matches
    constituent_skills: list[str] = Field(default_factory=list)  # canonical names
    key_evidence: str = ""     # best evidence sentence from matched skills
    importance: str = "important"  # critical | important | secondary (dominant)


# ── Stage D3: Parsed Resume ───────────────────────────────────────────────────

class ParsedSkillEntry(BaseModel):
    """A skill extracted from a resume."""
    name: str = Field(..., min_length=1)
    canonical_name: str = Field(..., min_length=1)
    proficiency: str = "intermediate"  # expert | advanced | intermediate | beginner
    evidence: str = Field("", description="Specific evidence from resume text")
    parent_category: Optional[str] = None

    @field_validator("name", "canonical_name", mode="before")
    @classmethod
    def strip_name(cls, v):
        return str(v).strip() if v else v


class ParsedExperienceEntry(BaseModel):
    title: str = ""
    company: str = ""
    duration: str = ""
    duration_years: Optional[float] = None  # Derived, not from LLM
    highlights: list[str] = Field(default_factory=list)


class ParsedEducationEntry(BaseModel):
    degree: str = ""
    field: str = ""
    institution: str = ""
    year: Optional[str] = None


class ParsedResume(BaseModel):
    """Output of Stage D3 (resume parsing)."""
    name: str = "Unknown"
    email: Optional[str] = None
    phone: Optional[str] = None
    summary: str = ""
    skills: list[ParsedSkillEntry] = Field(default_factory=list)
    experience: list[ParsedExperienceEntry] = Field(default_factory=list)
    total_experience_years: Optional[float] = None
    education: list[ParsedEducationEntry] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    notable_achievements: list[str] = Field(default_factory=list)
    # Metadata
    parsed_from_llm: bool = True
    has_evidence: bool = False  # True if any skill has non-empty evidence

    @model_validator(mode="after")
    def compute_has_evidence(self):
        self.has_evidence = any(s.evidence.strip() for s in self.skills)
        return self

    @property
    def skill_canonical_names(self) -> set[str]:
        return {s.canonical_name for s in self.skills}

    def get_skill_by_canonical(self, canonical_name: str) -> Optional[ParsedSkillEntry]:
        for s in self.skills:
            if s.canonical_name == canonical_name:
                return s
        return None


# ── Stage D4a: Skill Match ────────────────────────────────────────────────────

class SkillMatchResult(BaseModel):
    """Deterministic match result for a single required skill."""
    # Requirement info
    required_skill: str = Field(..., description="Canonical required skill name")
    importance: SkillImportance = SkillImportance.IMPORTANT
    capability_label: Optional[str] = None  # Original JD capability area (for UI grouping)

    # Match result (deterministic)
    match_level: MatchLevel = MatchLevel.MISSING
    matched_skill: Optional[str] = Field(None, description="Matched canonical name in resume")
    evidence: str = Field("", description="Evidence from resume (required for non-missing)")
    match_reason: str = Field("", description="Why this match level was assigned")

    # Scores (deterministic)
    skill_score: float = Field(0.0, ge=0.0, le=1.0)  # 0–1 contribution

    @model_validator(mode="after")
    def validate_evidence_for_non_missing(self):
        if self.match_level != MatchLevel.MISSING and not self.evidence.strip():
            # Downgrade: no evidence → weak at best
            if self.match_level in (MatchLevel.STRONG, MatchLevel.PARTIAL):
                self.match_level = MatchLevel.WEAK
                self.match_reason = f"[downgraded: no evidence] {self.match_reason}"
        return self



# ── Stage D4b: Experience Assessment ─────────────────────────────────────────

class ExperienceAssessment(BaseModel):
    """Deterministic experience assessment."""
    meets_requirements: bool = False
    years_candidate: Optional[float] = None
    years_required_min: Optional[float] = None
    years_match: YearsMatch = YearsMatch.UNKNOWN
    relevance: Relevance = Relevance.UNKNOWN
    evidence: str = ""
    score: float = Field(50.0, ge=0.0, le=100.0)


# ── Stage D4c: Education Assessment ──────────────────────────────────────────

class EducationAssessment(BaseModel):
    """Deterministic education assessment."""
    meets_requirements: bool = False
    level_match: YearsMatch = YearsMatch.UNKNOWN
    field_relevance: Relevance = Relevance.UNKNOWN
    evidence: str = ""
    score: float = Field(50.0, ge=0.0, le=100.0)


# ── Stage D4d: Execution Capability Assessment ───────────────────────────────

class ExecutionCapabilityAssessment(BaseModel):
    """Keyword-signal-based execution capability assessment from resume text.

    Four sub-dimensions evaluated against resume experience highlights,
    achievements, and skill evidence — no LLM call required.

    Sub-scores are 0–100. Confidence is capped at 'medium' because keyword
    detection is a proxy signal, not a structured LLM assessment.
    """
    system_design_score: float = Field(0.0, ge=0.0, le=100.0)
    project_ownership_score: float = Field(0.0, ge=0.0, le=100.0)
    leadership_score: float = Field(0.0, ge=0.0, le=100.0)
    production_scale_score: float = Field(0.0, ge=0.0, le=100.0)
    composite_score: float = Field(0.0, ge=0.0, le=100.0)
    confidence: str = "low"          # "medium" | "low"  (never "high" — proxy signal)
    evidence_text_length: int = 0    # total chars scanned — transparency
    signals_found: list[str] = Field(default_factory=list)  # which dimensions had hits


# ── Stage D4e: Gap Analysis ───────────────────────────────────────────────────

class GapEntry(BaseModel):
    """A single identified gap with severity classification."""
    skill: str = Field(..., description="The missing/weak canonical skill")
    severity: GapSeverity = GapSeverity.IMPORTANT
    description: str = Field("", description="Human-readable gap description")
    impact: str = Field("", description="Why this gap matters for the role")

    @field_validator("description", "impact", mode="before")
    @classmethod
    def ensure_str(cls, v):
        return str(v) if v else ""


class StrengthEntry(BaseModel):
    """A candidate strength backed by evidence."""
    description: str = Field(..., description="Human-readable strength")
    evidence: str = Field("", description="Specific evidence from resume")
    skill: Optional[str] = None  # Canonical skill name if skill-based

    @field_validator("description", "evidence", mode="before")
    @classmethod
    def ensure_str(cls, v):
        return str(v) if v else ""


# ── Stage D4e: Decision Trace Step ───────────────────────────────────────────

class TraceStep(BaseModel):
    """A single reasoning step in the decision trace."""
    step: int
    signal: str = Field(..., description="What signal was examined")
    finding: str = Field(..., description="What was found")
    impact: str = Field("neutral", description="positive | negative | neutral")
    weight: Optional[float] = None  # Weight this step contributes to final score


# ── Final Evaluation Output ───────────────────────────────────────────────────

class EvaluationOutput(BaseModel):
    """The canonical evaluation output contract.

    All fields are:
    - deterministic (same inputs → same outputs)
    - signal-derived (traceable to resume / JD evidence)
    - validated (evidence required for skill claims)
    """
    # Core decision (deterministic)
    recommendation: Recommendation
    confidence: float = Field(..., ge=0.0, le=1.0)
    composite_score: float = Field(..., ge=0.0, le=100.0)

    # Signals
    skill_matches: list[SkillMatchResult] = Field(default_factory=list)
    experience_assessment: ExperienceAssessment = Field(default_factory=ExperienceAssessment)
    education_assessment: EducationAssessment = Field(default_factory=EducationAssessment)

    # Capability layer (D4c) — additive, non-breaking
    capability_assessments: list[CapabilityAssessment] = Field(default_factory=list)

    # Execution capability (D4d) — keyword-signal assessment, additive, non-breaking
    execution_capability: ExecutionCapabilityAssessment = Field(
        default_factory=ExecutionCapabilityAssessment
    )

    # Explainability (derived from signals)
    strengths: list[StrengthEntry] = Field(default_factory=list)
    gaps: list[GapEntry] = Field(default_factory=list)
    explanation: str = Field("", description="Signal-derived human-readable explanation")
    decision_trace: list[TraceStep] = Field(default_factory=list)
    suggested_actions: list[str] = Field(default_factory=list)

    # Confidence components (for calibration transparency)
    evidence_density: float = Field(0.0, ge=0.0, le=1.0)
    signal_consistency: float = Field(0.0, ge=0.0, le=1.0)
    gap_severity_score: float = Field(0.0, ge=0.0, le=1.0)  # 0=many critical gaps, 1=none

    # Debug metadata (non-UI, structured)
    debug_metadata: dict = Field(default_factory=dict)
    trace_id: str = ""

    @property
    def critical_gaps(self) -> list[GapEntry]:
        return [g for g in self.gaps if g.severity == GapSeverity.CRITICAL]

    @property
    def has_critical_gaps(self) -> bool:
        return len(self.critical_gaps) > 0

    def to_db_dict(self) -> dict:
        """Convert to a dict suitable for persisting to the Evaluation model."""
        return {
            "recommendation": self.recommendation.value,
            "confidence": self.confidence,
            "composite_score": self.composite_score,
            "skill_matches": [
                {
                    "skill": m.required_skill,
                    "match_level": m.match_level.value,
                    "evidence": m.evidence,
                    "importance": m.importance.value,
                    "match_reason": m.match_reason,
                    "skill_score": m.skill_score,
                    "capability_label": getattr(m, "capability_label", None),
                }
                for m in self.skill_matches
            ],
            "experience_assessment": {
                "meets_requirements": self.experience_assessment.meets_requirements,
                "years_match": self.experience_assessment.years_match.value,
                "relevance": self.experience_assessment.relevance.value,
                "evidence": self.experience_assessment.evidence,
                "score": self.experience_assessment.score,
                "years_candidate": self.experience_assessment.years_candidate,
                "years_required_min": self.experience_assessment.years_required_min,
            },
            "education_assessment": {
                "meets_requirements": self.education_assessment.meets_requirements,
                "level_match": self.education_assessment.level_match.value,
                "field_relevance": self.education_assessment.field_relevance.value,
                "evidence": self.education_assessment.evidence,
                "score": self.education_assessment.score,
            },
            "strengths": [
                {
                    "description": s.description,
                    "evidence": s.evidence,
                    "skill": s.skill,
                }
                for s in self.strengths
            ],
            "gaps": [
                {
                    "skill": g.skill,
                    "severity": g.severity.value,
                    "description": g.description,
                    "impact": g.impact,
                }
                for g in self.gaps
            ],
            "explanation": self.explanation,
            "decision_trace": [
                {
                    "step": t.step,
                    "signal": t.signal,
                    "finding": t.finding,
                    "impact": t.impact,
                    "weight": t.weight,
                }
                for t in self.decision_trace
            ],
            "suggested_actions": self.suggested_actions,
            "debug_metadata": {
                **self.debug_metadata,
                "evidence_density": self.evidence_density,
                "signal_consistency": self.signal_consistency,
                "gap_severity_score": self.gap_severity_score,
                "critical_gap_count": len(self.critical_gaps),
                "trace_id": self.trace_id,
                # Execution capability stored in debug_metadata (no dedicated ORM column)
                "execution_capability": {
                    "system_design_score": self.execution_capability.system_design_score,
                    "project_ownership_score": self.execution_capability.project_ownership_score,
                    "leadership_score": self.execution_capability.leadership_score,
                    "production_scale_score": self.execution_capability.production_scale_score,
                    "composite_score": self.execution_capability.composite_score,
                    "confidence": self.execution_capability.confidence,
                    "evidence_text_length": self.execution_capability.evidence_text_length,
                    "signals_found": self.execution_capability.signals_found,
                },
                "capability_assessments": [
                    {
                        "capability": ca.capability,
                        "level": ca.level.value,
                        "score": ca.score,
                        "total_skills": ca.total_skills,
                        "matched_skills": ca.matched_skills,
                        "constituent_skills": ca.constituent_skills,
                        "key_evidence": ca.key_evidence,
                        "importance": ca.importance,
                    }
                    for ca in self.capability_assessments
                ],
            },
        }
