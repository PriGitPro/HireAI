"""Matching Engine — deterministic requirement-level skill classification.

Given a ParsedJobDescription (D2) and a ParsedResume (D3), produces:
  - SkillMatchResult for every required skill
  - ExperienceAssessment
  - EducationAssessment
  - GapEntry list (with severity classification)
  - StrengthEntry list (evidence-backed)

D4d Execution Capability uses an LLM assessment (assess_execution_capability_llm)
with keyword-heuristic fallback (assess_execution_capability) for offline/error cases.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from app.services.llm_provider import LLMProvider

from app.services.ontology import (
    canonicalize,
    get_implied_skills,
    get_parent_category,
    skills_share_parent,
)
from app.services.pipeline_schemas import (
    CapabilityAssessment,
    CapabilityLevel,
    EducationAssessment,
    ExecutionCapabilityAssessment,
    ExperienceAssessment,
    GapEntry,
    GapSeverity,
    MatchLevel,
    ParsedJobDescription,
    ParsedResume,
    ParsedSkillRequirement,
    Relevance,
    SkillImportance,
    SkillMatchResult,
    StrengthEntry,
    YearsMatch,
)

logger = logging.getLogger("hireai.matching")

# ── Scoring constants (named, no magic numbers) ───────────────────────────────

MATCH_SCORE: dict[MatchLevel, float] = {
    MatchLevel.STRONG: 1.0,
    MatchLevel.PARTIAL: 0.6,
    MatchLevel.WEAK: 0.25,
    MatchLevel.MISSING: 0.0,
}

IMPORTANCE_WEIGHT: dict[SkillImportance, float] = {
    SkillImportance.CRITICAL: 1.0,
    SkillImportance.IMPORTANT: 0.6,
    SkillImportance.SECONDARY: 0.3,
}

PROFICIENCY_SCORE: dict[str, float] = {
    "expert": 1.0,
    "advanced": 0.85,
    "intermediate": 0.65,
    "beginner": 0.35,
}

EDUCATION_LEVELS: dict[str, int] = {
    "none": 0, "high school": 1, "associate": 2,
    "bachelor": 3, "master": 4, "phd": 5, "doctorate": 5,
}

# ── Execution Capability keyword pools ────────────────────────────────────────
# Each pool targets one sub-dimension. Scoring: hit_rate = min(1, hits / (len*0.3))
# so ~30% of the keyword list is a full hit, avoiding over-sensitivity.

_KW_SYSTEM_DESIGN = [
    "architect", "architecture", "design pattern", "microservice", "system design",
    "distributed system", "scalab", "api design", "schema design", "infrastructure",
    "technical lead", "led design", "designed the", "service mesh", "event-driven",
    "domain-driven", "data model", "platform design",
]
_KW_PROJECT_OWNERSHIP = [
    "owned", "led", "built", "launched", "delivered", "drove", "responsible for",
    "managed the", "end-to-end", "from scratch", "solo", "founded", "spearheaded",
    "initiated", "shipped", "created", "developed and deployed", "took ownership",
]
_KW_LEADERSHIP = [
    "managed", "mentored", "coached", "hired", "grew the team", "cross-functional",
    "stakeholder", "director", "vp ", "head of", "team of", "reports to",
    "line manager", "people manager", "led a team", "managed a team", "tech lead",
]
_KW_PRODUCTION_SCALE = [
    "million user", "billion", "10x", "high traffic", "high availability", "99.",
    "production", "enterprise", "global", "petabyte", "terabyte", "at scale",
    "millions of", "thousands of", "large-scale", "mission-critical", "zero downtime",
]


def _kw_hit_rate(blob: str, keywords: list[str]) -> float:
    """Return normalised keyword hit rate (0–1) for a given text blob.

    Threshold: need ~30% of keywords to hit for a rate of 1.0.
    This avoids single-keyword matches inflating the score.
    """
    if not blob:
        return 0.0
    blob_lower = blob.lower()
    hits = sum(1 for kw in keywords if kw.lower() in blob_lower)
    threshold = max(len(keywords) * 0.3, 1)
    return min(1.0, hits / threshold)


# ── Public API ────────────────────────────────────────────────────────────────

def match_skills(
    jd: ParsedJobDescription,
    resume: ParsedResume,
) -> list[SkillMatchResult]:
    """Deterministically match every required skill against the resume.

    Algorithm per skill:
      1. Direct canonical match (strong)
      2. Implied-by match (partial)
      3. Same-parent-category match (weak)
      4. No match (missing)
    Evidence is taken from the resume's skill entry when available.
    """
    results: list[SkillMatchResult] = []
    candidate_canonical = {s.canonical_name for s in resume.skills}

    for req_skill in jd.required_skills:
        result = _match_single_skill(req_skill, resume, candidate_canonical)
        results.append(result)
        logger.debug(
            f"MATCH | {req_skill.canonical_name} ({req_skill.importance.value})"
            f" → {result.match_level.value} | score={result.skill_score:.2f}"
        )

    return results


def assess_capabilities(
    jd: ParsedJobDescription,
    skill_matches: list[SkillMatchResult],
) -> list[CapabilityAssessment]:
    """Aggregate skill-level matches into capability-area assessments.

    Strategy:
    - Group SkillMatchResults by their capability_label (set during JD parsing).
    - If a skill has no capability_label, group it under its parent_category,
      or fall back to 'General Requirements'.
    - For each group compute an aggregate score (weighted by importance) and
      a CapabilityLevel threshold.

    This is stage D4c — deterministic, zero LLM calls.
    Order: most-important / worst-covered first (helps recruiters triage).
    """
    from collections import defaultdict

    # Build lookup: canonical_name -> SkillMatchResult
    match_by_skill: dict[str, SkillMatchResult] = {
        m.required_skill: m for m in skill_matches
    }

    # Group JD requirements by capability label
    groups: dict[str, list[ParsedSkillRequirement]] = defaultdict(list)
    for req in jd.required_skills:
        key = (
            req.capability_label
            or (f"[{req.parent_category.title()}]" if req.parent_category else "General Requirements")
        )
        groups[key].append(req)

    results: list[CapabilityAssessment] = []

    SCORE_MAP = {
        MatchLevel.STRONG:  1.0,
        MatchLevel.PARTIAL: 0.6,
        MatchLevel.WEAK:    0.2,
        MatchLevel.MISSING: 0.0,
    }
    IMP_WEIGHT = {
        SkillImportance.CRITICAL:  1.5,
        SkillImportance.IMPORTANT: 1.0,
        SkillImportance.SECONDARY: 0.5,
    }

    for cap_label, reqs in groups.items():
        total = len(reqs)
        weighted_score_sum = 0.0
        weight_sum = 0.0
        matched = 0
        best_evidence = ""
        constituent_skills: list[str] = []

        # Dominant importance: critical > important > secondary
        importance_rank = {"critical": 0, "important": 1, "secondary": 2}
        dominant_importance = min(
            (r.importance.value for r in reqs),
            key=lambda v: importance_rank.get(v, 1),
        )

        for req in reqs:
            constituent_skills.append(req.canonical_name)
            m = match_by_skill.get(req.canonical_name)
            if m is None:
                continue  # shouldn't happen but defensive

            w = IMP_WEIGHT.get(req.importance, 1.0)
            s = SCORE_MAP.get(m.match_level, 0.0)
            weighted_score_sum += s * w
            weight_sum += w

            if m.match_level in (MatchLevel.STRONG, MatchLevel.PARTIAL):
                matched += 1
                if m.evidence and len(m.evidence) > len(best_evidence):
                    best_evidence = m.evidence

        # Aggregate score 0–100
        raw_score = (weighted_score_sum / weight_sum) if weight_sum else 0.0
        score = round(raw_score * 100, 1)

        # Capability level thresholds
        match_pct = (matched / total) if total else 0.0
        if match_pct >= 0.70:
            level = CapabilityLevel.STRONG
        elif match_pct >= 0.40:
            level = CapabilityLevel.PARTIAL
        elif match_pct >= 0.10:
            level = CapabilityLevel.WEAK
        else:
            level = CapabilityLevel.MISSING

        results.append(CapabilityAssessment(
            capability=cap_label,
            level=level,
            score=score,
            total_skills=total,
            matched_skills=matched,
            constituent_skills=constituent_skills,
            key_evidence=best_evidence[:200] if best_evidence else "",
            importance=dominant_importance,
        ))

        logger.debug(
            f"CAPABILITY | '{cap_label}'"
            f" → {level.value} | score={score:.0f}"
            f" | matched={matched}/{total}"
        )

    # Sort: critical first, then by score ascending (worst first — triage order)
    def _sort_key(ca: CapabilityAssessment):
        imp_order = {"critical": 0, "important": 1, "secondary": 2}
        return (imp_order.get(ca.importance, 1), ca.score)

    results.sort(key=_sort_key)
    return results


def assess_execution_capability(resume: ParsedResume) -> ExecutionCapabilityAssessment:
    """Assess execution capability via keyword signals on resume text.

    Sources scanned (richest text first):
      - Experience role highlights (most signal-dense)
      - Notable achievements
      - Skill evidence strings
      - Role titles + company names
      - Resume summary

    Sub-scores:
      system_design    — architecture, distributed systems, platform design
      project_ownership — end-to-end delivery, ownership language
      leadership       — team management, mentoring, cross-functional
      production_scale — scale indicators, enterprise, high availability

    Composite: 0.35 * design + 0.30 * ownership + 0.20 * leadership + 0.15 * scale

    Confidence is capped at 'medium' — keyword signals are proxies, not truth.
    Only a structured LLM assessment would warrant 'high' confidence.
    """
    # Gather all resume text into a single blob
    text_parts: list[str] = [resume.summary]

    for exp in resume.experience:
        if exp.title:
            text_parts.append(exp.title)
        if exp.company:
            text_parts.append(exp.company)
        text_parts.extend(exp.highlights)

    text_parts.extend(resume.notable_achievements)
    text_parts.extend(s.evidence for s in resume.skills if s.evidence.strip())

    blob = " ".join(p for p in text_parts if p)
    evidence_text_length = len(blob)

    # Score each sub-dimension
    sys_design = _kw_hit_rate(blob, _KW_SYSTEM_DESIGN)
    ownership  = _kw_hit_rate(blob, _KW_PROJECT_OWNERSHIP)
    leadership = _kw_hit_rate(blob, _KW_LEADERSHIP)
    prod_scale = _kw_hit_rate(blob, _KW_PRODUCTION_SCALE)

    composite = 0.35 * sys_design + 0.30 * ownership + 0.20 * leadership + 0.15 * prod_scale

    # Track which dimensions had any signal
    signals_found: list[str] = []
    if sys_design > 0:
        signals_found.append("system_design")
    if ownership > 0:
        signals_found.append("project_ownership")
    if leadership > 0:
        signals_found.append("leadership")
    if prod_scale > 0:
        signals_found.append("production_scale")

    # Confidence: medium only if resume is rich AND multiple dimensions fired
    if evidence_text_length > 800 and len(signals_found) >= 3:
        confidence = "medium"
    else:
        confidence = "low"

    logger.debug(
        f"EXEC_CAP | design={sys_design:.2f} own={ownership:.2f}"
        f" lead={leadership:.2f} scale={prod_scale:.2f}"
        f" composite={composite:.2f} conf={confidence}"
        f" evidence_len={evidence_text_length}"
    )

    return ExecutionCapabilityAssessment(
        system_design_score=round(sys_design * 100, 1),
        project_ownership_score=round(ownership * 100, 1),
        leadership_score=round(leadership * 100, 1),
        production_scale_score=round(prod_scale * 100, 1),
        composite_score=round(composite * 100, 1),
        confidence=confidence,
        evidence_text_length=evidence_text_length,
        signals_found=signals_found,
        assessment_method="keyword",
    )


async def assess_execution_capability_llm(
    resume: ParsedResume,
    llm_provider: "LLMProvider",
) -> ExecutionCapabilityAssessment:
    """Assess execution capability using LLM reasoning with keyword fallback.

    Primary path: structured LLM assessment over the full resume text.
    The LLM reads experience highlights, achievements, and skill evidence
    and scores four dimensions with cited evidence, enabling 'high' confidence.

    Fallback: if the LLM call fails or returns malformed JSON, falls back to
    the keyword heuristic (assess_execution_capability) which caps at 'medium'.
    """
    from app.services.prompts import EXECUTION_CAPABILITY_PROMPT

    # Build rich resume text (same blob used by keyword fallback)
    text_parts: list[str] = [resume.summary]
    for exp in resume.experience:
        if exp.title:
            text_parts.append(exp.title)
        if exp.company:
            text_parts.append(exp.company)
        text_parts.extend(exp.highlights)
    text_parts.extend(resume.notable_achievements)
    text_parts.extend(s.evidence for s in resume.skills if s.evidence.strip())
    blob = "\n".join(p for p in text_parts if p)

    prompt = EXECUTION_CAPABILITY_PROMPT.format(resume_text=blob)

    try:
        response = await llm_provider.generate(prompt, force_json=True)
        raw = response.content.strip()

        # Strip markdown fences if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip()

        data = json.loads(raw)

        def _clamp(v, lo=0.0, hi=100.0) -> float:
            try:
                return max(lo, min(hi, float(v)))
            except (TypeError, ValueError):
                return 0.0

        sys_d  = _clamp(data.get("system_design_score", 0))
        own    = _clamp(data.get("project_ownership_score", 0))
        lead   = _clamp(data.get("leadership_score", 0))
        scale  = _clamp(data.get("production_scale_score", 0))

        # Recompute composite from sub-scores rather than trusting LLM arithmetic
        composite = 0.35 * sys_d + 0.30 * own + 0.20 * lead + 0.15 * scale

        raw_conf = str(data.get("confidence", "low")).lower()
        confidence = raw_conf if raw_conf in ("high", "medium", "low") else "medium"

        signals_found: list[str] = [
            s for s in data.get("signals_found", [])
            if s in ("system_design", "project_ownership", "leadership", "production_scale")
        ]
        # Rebuild signals_found from scored dimensions if LLM omitted them
        if not signals_found:
            if sys_d >= 30:
                signals_found.append("system_design")
            if own >= 30:
                signals_found.append("project_ownership")
            if lead >= 30:
                signals_found.append("leadership")
            if scale >= 30:
                signals_found.append("production_scale")

        dim_evidence: dict[str, str] = data.get("dimension_evidence", {})
        if not isinstance(dim_evidence, dict):
            dim_evidence = {}

        logger.info(
            f"EXEC_CAP(LLM) | design={sys_d:.0f} own={own:.0f}"
            f" lead={lead:.0f} scale={scale:.0f}"
            f" composite={composite:.0f} conf={confidence}"
        )

        return ExecutionCapabilityAssessment(
            system_design_score=round(sys_d, 1),
            project_ownership_score=round(own, 1),
            leadership_score=round(lead, 1),
            production_scale_score=round(scale, 1),
            composite_score=round(composite, 1),
            confidence=confidence,
            evidence_text_length=len(blob),
            signals_found=signals_found,
            dimension_evidence=dim_evidence,
            assessment_method="llm",
        )

    except Exception as exc:
        logger.warning(
            f"EXEC_CAP(LLM) failed ({type(exc).__name__}: {exc})"
            " — falling back to keyword heuristic"
        )
        return assess_execution_capability(resume)


def assess_experience(
    jd: ParsedJobDescription,
    resume: ParsedResume,
) -> ExperienceAssessment:
    """Deterministically assess candidate experience against JD requirements."""
    req = jd.experience_requirements
    candidate_years = resume.total_experience_years

    # Build evidence from experience entries
    evidence_parts = []
    for exp in resume.experience[:3]:
        if exp.title or exp.company:
            part = f"{exp.title} at {exp.company}" if exp.company else exp.title
            if exp.duration:
                part += f" ({exp.duration})"
            evidence_parts.append(part)
    evidence = "; ".join(evidence_parts) if evidence_parts else ""

    # Years match
    years_match = YearsMatch.UNKNOWN
    if candidate_years is not None and req.min_years is not None:
        if candidate_years >= (req.min_years * 1.2):
            years_match = YearsMatch.EXCEEDS
        elif candidate_years >= req.min_years:
            years_match = YearsMatch.MEETS
        else:
            years_match = YearsMatch.BELOW
    elif candidate_years is not None:
        years_match = YearsMatch.MEETS  # No explicit requirement

    # Relevance: check if preferred areas appear in resume text
    relevance = Relevance.UNKNOWN
    if req.preferred_areas:
        resume_text_lower = " ".join(
            f"{e.title} {e.company} {' '.join(e.highlights)}"
            for e in resume.experience
        ).lower()
        matched_areas = sum(
            1 for area in req.preferred_areas
            if area.lower() in resume_text_lower
        )
        ratio = matched_areas / len(req.preferred_areas)
        if ratio >= 0.6:
            relevance = Relevance.HIGH
        elif ratio >= 0.3:
            relevance = Relevance.MEDIUM
        else:
            relevance = Relevance.LOW
    elif resume.experience:
        relevance = Relevance.MEDIUM

    # Score calculation
    score = _experience_score(years_match, relevance, candidate_years, req.min_years)

    meets = years_match in (YearsMatch.MEETS, YearsMatch.EXCEEDS, YearsMatch.UNKNOWN)

    return ExperienceAssessment(
        meets_requirements=meets,
        years_candidate=candidate_years,
        years_required_min=req.min_years,
        years_match=years_match,
        relevance=relevance,
        evidence=evidence,
        score=score,
    )


def assess_education(
    jd: ParsedJobDescription,
    resume: ParsedResume,
) -> EducationAssessment:
    """Deterministically assess education against JD requirements."""
    req = jd.education_requirements
    education = resume.education

    if not education:
        # No education info — neutral unless requirement is explicit
        required_level = EDUCATION_LEVELS.get(req.min_level.lower(), 0)
        meets = required_level == 0
        return EducationAssessment(
            meets_requirements=meets,
            level_match=YearsMatch.UNKNOWN,
            field_relevance=Relevance.UNKNOWN,
            evidence="No education information found in resume",
            score=40.0 if not meets else 60.0,
        )

    # Find highest education level
    highest_entry = None
    highest_level = -1
    for edu in education:
        degree_normalized = edu.degree.lower()
        for level_name, level_val in EDUCATION_LEVELS.items():
            if level_name in degree_normalized and level_val > highest_level:
                highest_level = level_val
                highest_entry = edu

    required_level = EDUCATION_LEVELS.get(req.min_level.lower(), 0)

    # Level match
    if highest_level == -1:
        level_match = YearsMatch.UNKNOWN
    elif highest_level > required_level:
        level_match = YearsMatch.EXCEEDS
    elif highest_level >= required_level:
        level_match = YearsMatch.MEETS
    else:
        level_match = YearsMatch.BELOW

    # Field relevance
    field_relevance = Relevance.UNKNOWN
    if req.preferred_fields and highest_entry:
        field_lower = highest_entry.field.lower()
        for preferred in req.preferred_fields:
            if preferred.lower() in field_lower or field_lower in preferred.lower():
                field_relevance = Relevance.HIGH
                break
        if field_relevance == Relevance.UNKNOWN:
            field_relevance = Relevance.LOW
    elif highest_entry:
        field_relevance = Relevance.MEDIUM

    # Evidence
    evidence = ""
    if highest_entry:
        parts = [p for p in [highest_entry.degree, highest_entry.field, highest_entry.institution] if p]
        evidence = " — ".join(parts)

    meets = level_match in (YearsMatch.MEETS, YearsMatch.EXCEEDS, YearsMatch.UNKNOWN)
    score = _education_score(level_match, field_relevance)

    return EducationAssessment(
        meets_requirements=meets,
        level_match=level_match,
        field_relevance=field_relevance,
        evidence=evidence,
        score=score,
    )


def build_gaps(
    skill_matches: list[SkillMatchResult],
    experience: ExperienceAssessment,
) -> list[GapEntry]:
    """Derive gap list from skill matches and experience assessment.

    Gap severity:
      - CRITICAL: missing/weak critical skill
      - IMPORTANT: missing important skill, or clearly below experience req
      - MINOR: weak match on important skill, or secondary gaps
    """
    gaps: list[GapEntry] = []

    for sm in skill_matches:
        if sm.match_level == MatchLevel.MISSING:
            if sm.importance == SkillImportance.CRITICAL:
                severity = GapSeverity.CRITICAL
                impact = "Critical requirement not met — directly blocks role performance"
            elif sm.importance == SkillImportance.IMPORTANT:
                severity = GapSeverity.IMPORTANT
                impact = "Significant gap — will require ramp-up time"
            else:
                severity = GapSeverity.MINOR
                impact = "Nice-to-have not present"

            gaps.append(GapEntry(
                skill=sm.required_skill,
                severity=severity,
                description=f"{sm.required_skill} not found in resume",
                impact=impact,
            ))

        elif sm.match_level == MatchLevel.WEAK:
            if sm.importance == SkillImportance.CRITICAL:
                severity = GapSeverity.CRITICAL
                impact = "Critical skill only weakly evidenced — significant risk"
            elif sm.importance == SkillImportance.IMPORTANT:
                severity = GapSeverity.IMPORTANT
                impact = "Proficiency level uncertain — verify in interview"
            else:
                severity = GapSeverity.MINOR
                impact = "Limited evidence of proficiency"

            gaps.append(GapEntry(
                skill=sm.required_skill,
                severity=severity,
                description=f"{sm.required_skill} weakly evidenced in resume",
                impact=impact,
            ))

    # Experience gap
    if experience.years_match == YearsMatch.BELOW and experience.years_required_min:
        candidate_y = experience.years_candidate or 0
        required_y = experience.years_required_min
        delta = required_y - candidate_y
        severity = GapSeverity.CRITICAL if delta > 2 else GapSeverity.IMPORTANT
        gaps.append(GapEntry(
            skill="Experience",
            severity=severity,
            description=(
                f"Candidate has ~{candidate_y:.0f} years vs "
                f"{required_y:.0f}+ required"
            ),
            impact=f"Under-experienced by ~{delta:.0f} years",
        ))

    # Sort: critical first, then important, then minor
    severity_order = {GapSeverity.CRITICAL: 0, GapSeverity.IMPORTANT: 1, GapSeverity.MINOR: 2}
    gaps.sort(key=lambda g: severity_order[g.severity])
    return gaps


def build_strengths(
    skill_matches: list[SkillMatchResult],
    experience: ExperienceAssessment,
    resume: ParsedResume,
) -> list[StrengthEntry]:
    """Derive evidence-backed strengths from matching signals."""
    strengths: list[StrengthEntry] = []

    # Strong critical skills are top strengths
    strong_critical = [
        sm for sm in skill_matches
        if sm.match_level == MatchLevel.STRONG
        and sm.importance == SkillImportance.CRITICAL
        and sm.evidence.strip()
    ]
    for sm in strong_critical[:3]:
        strengths.append(StrengthEntry(
            description=f"Strong {sm.required_skill} proficiency (critical requirement met)",
            evidence=sm.evidence,
            skill=sm.required_skill,
        ))

    # Strong important skills
    strong_important = [
        sm for sm in skill_matches
        if sm.match_level == MatchLevel.STRONG
        and sm.importance == SkillImportance.IMPORTANT
        and sm.evidence.strip()
        and sm not in strong_critical
    ]
    for sm in strong_important[:2]:
        strengths.append(StrengthEntry(
            description=f"Demonstrated {sm.required_skill} experience",
            evidence=sm.evidence,
            skill=sm.required_skill,
        ))

    # Experience exceeds requirements
    if experience.years_match == YearsMatch.EXCEEDS and experience.evidence:
        req_y = experience.years_required_min or 0
        cand_y = experience.years_candidate or 0
        strengths.append(StrengthEntry(
            description=f"Experience exceeds requirements ({cand_y:.0f} vs {req_y:.0f}+ years)",
            evidence=experience.evidence,
        ))

    # Notable achievements from resume
    for achievement in resume.notable_achievements[:2]:
        if achievement.strip():
            strengths.append(StrengthEntry(
                description=achievement,
                evidence="From resume notable achievements",
            ))

    return strengths[:6]  # Cap at 6 strengths


def build_suggested_actions(
    gaps: list[GapEntry],
    skill_matches: list[SkillMatchResult],
    recommendation: str,
) -> list[str]:
    """Derive actionable next steps from gaps and weak signals."""
    actions: list[str] = []

    # Critical gap actions
    for gap in gaps:
        if gap.severity == GapSeverity.CRITICAL and gap.skill != "Experience":
            actions.append(
                f"Probe depth of {gap.skill} knowledge — this is a critical requirement"
            )

    # Partial/weak important skills → probe in interview
    partial_important = [
        sm for sm in skill_matches
        if sm.match_level in (MatchLevel.PARTIAL, MatchLevel.WEAK)
        and sm.importance == SkillImportance.IMPORTANT
    ]
    for sm in partial_important[:2]:
        actions.append(f"Assess {sm.required_skill} proficiency level in technical screen")

    # Experience below req
    exp_gap = next((g for g in gaps if g.skill == "Experience"), None)
    if exp_gap:
        if exp_gap.severity == GapSeverity.CRITICAL:
            actions.append("Clarify actual years of relevant experience — may be understated in resume")
        else:
            actions.append("Discuss pace of progression and depth of experience in interview")

    # If no_hire/consider — suggest alternatives
    if recommendation in ("no_hire", "consider") and not actions:
        actions.append("Consider for a more junior role or with additional skill development plan")

    # If strong_hire → suggest fast-track
    if recommendation == "strong_hire" and not actions:
        actions.append("Recommend for technical interview — strong signal across all requirements")

    return actions[:5]  # Cap at 5


# ── Private helpers ───────────────────────────────────────────────────────────

def _match_single_skill(
    req_skill: ParsedSkillRequirement,
    resume: ParsedResume,
    candidate_canonical: set[str],
) -> SkillMatchResult:
    """Match a single required skill against the resume."""
    canonical = req_skill.canonical_name
    importance = req_skill.importance
    cap_label = req_skill.capability_label  # Preserve for UI grouping

    # 1. Direct canonical match
    resume_entry = resume.get_skill_by_canonical(canonical)
    if resume_entry:
        proficiency_factor = PROFICIENCY_SCORE.get(resume_entry.proficiency, 0.65)
        match_level = _proficiency_to_match_level(resume_entry.proficiency)
        base_score = MATCH_SCORE[match_level] * proficiency_factor
        return SkillMatchResult(
            required_skill=canonical,
            importance=importance,
            capability_label=cap_label,
            match_level=match_level,
            matched_skill=resume_entry.canonical_name,
            evidence=resume_entry.evidence or f"Listed as {resume_entry.proficiency} in resume",
            match_reason=f"Direct match: {resume_entry.name} (proficiency: {resume_entry.proficiency})",
            skill_score=min(base_score * IMPORTANCE_WEIGHT[importance], 1.0),
        )

    # 2. Check if candidate has a skill that implies this one
    for candidate_skill in resume.skills:
        implied = get_implied_skills(candidate_skill.canonical_name)
        if canonical in implied:
            return SkillMatchResult(
                required_skill=canonical,
                importance=importance,
                capability_label=cap_label,
                match_level=MatchLevel.PARTIAL,
                matched_skill=candidate_skill.canonical_name,
                evidence=(
                    candidate_skill.evidence
                    or f"{candidate_skill.canonical_name} implies knowledge of {canonical}"
                ),
                match_reason=f"Implied by {candidate_skill.canonical_name}",
                skill_score=MATCH_SCORE[MatchLevel.PARTIAL] * IMPORTANCE_WEIGHT[importance],
            )

    # 3. Same parent category → weak match
    req_parent = get_parent_category(canonical)
    if req_parent:
        for candidate_skill in resume.skills:
            cand_parent = get_parent_category(candidate_skill.canonical_name)
            if cand_parent and cand_parent == req_parent:
                return SkillMatchResult(
                    required_skill=canonical,
                    importance=importance,
                    capability_label=cap_label,
                    match_level=MatchLevel.WEAK,
                    matched_skill=candidate_skill.canonical_name,
                    evidence=(
                        candidate_skill.evidence
                        or f"Has {candidate_skill.canonical_name} in same category ({req_parent})"
                    ),
                    match_reason=(
                        f"Category match: {candidate_skill.canonical_name}"
                        f" is in same '{req_parent}' category"
                    ),
                    skill_score=MATCH_SCORE[MatchLevel.WEAK] * IMPORTANCE_WEIGHT[importance],
                )

    # 4. No match
    return SkillMatchResult(
        required_skill=canonical,
        importance=importance,
        capability_label=cap_label,
        match_level=MatchLevel.MISSING,
        evidence="",
        match_reason="No matching skill found in resume",
        skill_score=0.0,
    )



def _proficiency_to_match_level(proficiency: str) -> MatchLevel:
    mapping = {
        "expert": MatchLevel.STRONG,
        "advanced": MatchLevel.STRONG,
        "intermediate": MatchLevel.PARTIAL,
        "beginner": MatchLevel.WEAK,
    }
    return mapping.get(proficiency.lower(), MatchLevel.PARTIAL)


def _experience_score(
    years_match: YearsMatch,
    relevance: Relevance,
    candidate_years: Optional[float],
    required_min: Optional[float],
) -> float:
    base = {
        YearsMatch.EXCEEDS: 90.0,
        YearsMatch.MEETS: 75.0,
        YearsMatch.BELOW: 35.0,
        YearsMatch.UNKNOWN: 60.0,
    }[years_match]

    relevance_adj = {
        Relevance.HIGH: 5.0,
        Relevance.MEDIUM: 0.0,
        Relevance.LOW: -10.0,
        Relevance.UNKNOWN: 0.0,
    }[relevance]

    return max(0.0, min(100.0, base + relevance_adj))


def _education_score(level_match: YearsMatch, field_relevance: Relevance) -> float:
    base = {
        YearsMatch.EXCEEDS: 95.0,
        YearsMatch.MEETS: 80.0,
        YearsMatch.BELOW: 40.0,
        YearsMatch.UNKNOWN: 60.0,
    }[level_match]

    relevance_adj = {
        Relevance.HIGH: 5.0,
        Relevance.MEDIUM: 0.0,
        Relevance.LOW: -5.0,
        Relevance.UNKNOWN: 0.0,
    }[field_relevance]

    return max(0.0, min(100.0, base + relevance_adj))
