"""Matching Engine — deterministic requirement-level skill classification.

Given a ParsedJobDescription (D2) and a ParsedResume (D3), produces:
  - SkillMatchResult for every required skill
  - ExperienceAssessment
  - EducationAssessment
  - GapEntry list (with severity classification)
  - StrengthEntry list (evidence-backed)

No LLM calls. All logic is rule-based and reproducible.
"""

from __future__ import annotations

import logging
from typing import Optional

from app.services.ontology import (
    canonicalize,
    get_implied_skills,
    get_parent_category,
    skills_share_parent,
)
from app.services.pipeline_schemas import (
    EducationAssessment,
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

    # 1. Direct canonical match
    resume_entry = resume.get_skill_by_canonical(canonical)
    if resume_entry:
        proficiency_factor = PROFICIENCY_SCORE.get(resume_entry.proficiency, 0.65)
        match_level = _proficiency_to_match_level(resume_entry.proficiency)
        base_score = MATCH_SCORE[match_level] * proficiency_factor
        return SkillMatchResult(
            required_skill=canonical,
            importance=importance,
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
