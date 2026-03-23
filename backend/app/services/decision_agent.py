"""Decision Agent — rule-based signal → decision engine.

Converts matching signals into a final EvaluationOutput with:
  - Deterministic recommendation tier (strong_hire/hire/consider/no_hire)
  - Calibrated confidence (evidence density × signal consistency × gap severity)
  - Composite score (weighted sum of signals)
  - Ordered decision trace
  - Signal-derived explanation text

No LLM calls. Decision is fully explainable from inputs.
"""

from __future__ import annotations

import logging
from typing import Optional

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
    YearsMatch,
)

logger = logging.getLogger("hireai.decision")

# ── Score weights (named constants, not magic numbers) ────────────────────────
WEIGHT_SKILLS = 0.40
WEIGHT_EXPERIENCE = 0.30
WEIGHT_EDUCATION = 0.15
WEIGHT_OVERALL_FIT = 0.15

# ── Recommendation thresholds ─────────────────────────────────────────────────
THRESHOLD_STRONG_HIRE = 78.0
THRESHOLD_HIRE = 62.0
THRESHOLD_CONSIDER = 42.0
# < THRESHOLD_CONSIDER → NO_HIRE

# ── Confidence components ─────────────────────────────────────────────────────
# Evidence density: fraction of required skills that have non-empty evidence
# Signal consistency: how well skill scores cluster (low variance = high consistency)
# Gap severity: penalized by critical gaps


class DecisionAgent:
    """Converts matching signals into a final deterministic evaluation."""

    def decide(
        self,
        jd: ParsedJobDescription,
        resume: ParsedResume,
        skill_matches: list[SkillMatchResult],
        experience: ExperienceAssessment,
        education: EducationAssessment,
        gaps: list[GapEntry],
        strengths: list[StrengthEntry],
        suggested_actions: list[str],
        trace_id: str = "",
    ) -> EvaluationOutput:
        trace: list[TraceStep] = []
        step = 1

        # ── Step 1: Skill score ───────────────────────────────────────────────
        skills_score = self._compute_skills_score(skill_matches)
        critical_missing = [
            sm for sm in skill_matches
            if sm.match_level == MatchLevel.MISSING
            and sm.importance == SkillImportance.CRITICAL
        ]
        critical_weak = [
            sm for sm in skill_matches
            if sm.match_level == MatchLevel.WEAK
            and sm.importance == SkillImportance.CRITICAL
        ]

        trace.append(TraceStep(
            step=step, signal="skill_match",
            finding=(
                f"{len(skill_matches)} skills evaluated — "
                f"score {skills_score:.0f}/100 "
                f"({len(critical_missing)} critical missing, "
                f"{len(critical_weak)} critical weak)"
            ),
            impact="negative" if skills_score < 50 else "positive" if skills_score >= 70 else "neutral",
            weight=WEIGHT_SKILLS,
        ))
        step += 1

        # ── Step 2: Experience ────────────────────────────────────────────────
        exp_score = experience.score
        trace.append(TraceStep(
            step=step, signal="experience",
            finding=(
                f"Experience assessment: {experience.years_match.value} "
                f"({experience.years_candidate or '?'} years candidate vs "
                f"{experience.years_required_min or 'no req'} required) — "
                f"score {exp_score:.0f}/100"
            ),
            impact="positive" if experience.meets_requirements else "negative",
            weight=WEIGHT_EXPERIENCE,
        ))
        step += 1

        # ── Step 3: Education ─────────────────────────────────────────────────
        edu_score = education.score
        trace.append(TraceStep(
            step=step, signal="education",
            finding=(
                f"Education: {education.level_match.value} — "
                f"field relevance {education.field_relevance.value} — "
                f"score {edu_score:.0f}/100"
            ),
            impact="positive" if education.meets_requirements else "neutral",
            weight=WEIGHT_EDUCATION,
        ))
        step += 1

        # ── Step 4: Overall fit (derived signal) ──────────────────────────────
        overall_fit_score = self._compute_overall_fit(
            skill_matches, experience, education
        )
        trace.append(TraceStep(
            step=step, signal="overall_fit",
            finding=(
                f"Overall fit signal: {overall_fit_score:.0f}/100 "
                f"(breadth of match + experience coherence)"
            ),
            impact="neutral",
            weight=WEIGHT_OVERALL_FIT,
        ))
        step += 1

        # ── Step 5: Composite score ───────────────────────────────────────────
        composite = (
            skills_score * WEIGHT_SKILLS
            + exp_score * WEIGHT_EXPERIENCE
            + edu_score * WEIGHT_EDUCATION
            + overall_fit_score * WEIGHT_OVERALL_FIT
        )
        trace.append(TraceStep(
            step=step, signal="composite_score",
            finding=(
                f"Weighted composite: "
                f"skills({skills_score:.0f}×{WEIGHT_SKILLS}) + "
                f"exp({exp_score:.0f}×{WEIGHT_EXPERIENCE}) + "
                f"edu({edu_score:.0f}×{WEIGHT_EDUCATION}) + "
                f"fit({overall_fit_score:.0f}×{WEIGHT_OVERALL_FIT}) "
                f"= {composite:.1f}"
            ),
            impact="neutral",
        ))
        step += 1

        # ── Step 6: Critical gap check (hard rules) ───────────────────────────
        n_critical_gaps = len([g for g in gaps if g.severity == GapSeverity.CRITICAL])

        if n_critical_gaps > 0:
            gap_names = ", ".join(g.skill for g in gaps if g.severity == GapSeverity.CRITICAL)
            trace.append(TraceStep(
                step=step, signal="critical_gap_check",
                finding=(
                    f"{n_critical_gaps} critical gap(s) detected: {gap_names}. "
                    f"Capping recommendation at 'consider' if score < {THRESHOLD_HIRE}"
                ),
                impact="negative",
            ))
            step += 1

        # ── Step 7: Final recommendation ──────────────────────────────────────
        recommendation = self._tier_recommendation(
            composite=composite,
            n_critical_missing=len(critical_missing),
            n_critical_weak=len(critical_weak),
            experience_meets=experience.meets_requirements,
        )

        trace.append(TraceStep(
            step=step, signal="recommendation",
            finding=(
                f"Recommendation: {recommendation.value} "
                f"(score {composite:.1f}, "
                f"{len(critical_missing)} critical missing, "
                f"experience_meets={experience.meets_requirements})"
            ),
            impact="positive" if recommendation in (Recommendation.STRONG_HIRE, Recommendation.HIRE) else "negative",
        ))

        # ── Confidence computation ────────────────────────────────────────────
        evidence_density = self._evidence_density(skill_matches)
        signal_consistency = self._signal_consistency(skill_matches)
        gap_severity_score = self._gap_severity_score(gaps)

        # Confidence = weighted combination
        raw_confidence = (
            evidence_density * 0.4
            + signal_consistency * 0.35
            + gap_severity_score * 0.25
        )
        # Clamp to [0.05, 0.97]
        confidence = max(0.05, min(0.97, raw_confidence))

        # ── Generate explanation ──────────────────────────────────────────────
        explanation = self._generate_explanation(
            recommendation=recommendation,
            composite=composite,
            skill_matches=skill_matches,
            experience=experience,
            gaps=gaps,
            strengths=strengths,
            jd=jd,
        )

        # ── Debug metadata ────────────────────────────────────────────────────
        debug_metadata = {
            "skills_score": round(skills_score, 2),
            "exp_score": round(exp_score, 2),
            "edu_score": round(edu_score, 2),
            "overall_fit_score": round(overall_fit_score, 2),
            "weights": {
                "skills": WEIGHT_SKILLS,
                "experience": WEIGHT_EXPERIENCE,
                "education": WEIGHT_EDUCATION,
                "overall_fit": WEIGHT_OVERALL_FIT,
            },
            "thresholds": {
                "strong_hire": THRESHOLD_STRONG_HIRE,
                "hire": THRESHOLD_HIRE,
                "consider": THRESHOLD_CONSIDER,
            },
            "critical_missing_count": len(critical_missing),
            "critical_weak_count": len(critical_weak),
            "total_skills_required": len(skill_matches),
            "skills_with_evidence": sum(1 for sm in skill_matches if sm.evidence.strip()),
        }

        logger.info(
            f"DECISION | recommendation={recommendation.value}"
            f" | composite={composite:.1f}"
            f" | confidence={confidence:.3f}"
            f" | critical_gaps={n_critical_gaps}"
            f" | evidence_density={evidence_density:.2f}"
        )

        return EvaluationOutput(
            recommendation=recommendation,
            confidence=confidence,
            composite_score=round(composite, 1),
            skill_matches=skill_matches,
            experience_assessment=experience,
            education_assessment=education,
            strengths=strengths,
            gaps=gaps,
            explanation=explanation,
            decision_trace=trace,
            suggested_actions=suggested_actions,
            evidence_density=evidence_density,
            signal_consistency=signal_consistency,
            gap_severity_score=gap_severity_score,
            debug_metadata=debug_metadata,
            trace_id=trace_id,
        )

    # ── Private helpers ───────────────────────────────────────────────────────

    def _compute_skills_score(self, skill_matches: list[SkillMatchResult]) -> float:
        """Weighted average of per-skill scores, emphasizing critical skills."""
        if not skill_matches:
            return 50.0

        total_weight = 0.0
        weighted_sum = 0.0

        MATCH_SCORE_PCT = {
            MatchLevel.STRONG: 100.0,
            MatchLevel.PARTIAL: 60.0,
            MatchLevel.WEAK: 25.0,
            MatchLevel.MISSING: 0.0,
        }
        IMPORTANCE_W = {
            SkillImportance.CRITICAL: 3.0,
            SkillImportance.IMPORTANT: 1.5,
            SkillImportance.SECONDARY: 0.5,
        }

        for sm in skill_matches:
            weight = IMPORTANCE_W[sm.importance]
            score = MATCH_SCORE_PCT[sm.match_level]
            weighted_sum += score * weight
            total_weight += weight

        return weighted_sum / total_weight if total_weight > 0 else 50.0

    def _compute_overall_fit(
        self,
        skill_matches: list[SkillMatchResult],
        experience: ExperienceAssessment,
        education: EducationAssessment,
    ) -> float:
        """Overall fit: breadth of match + experience coherence."""
        total = len(skill_matches)
        if total == 0:
            return 50.0

        matched = sum(1 for sm in skill_matches if sm.match_level != MatchLevel.MISSING)
        breadth = (matched / total) * 100.0

        # Bonus for experience exceeding requirements
        if experience.years_match == YearsMatch.EXCEEDS:
            breadth = min(100.0, breadth + 10.0)

        return breadth

    def _tier_recommendation(
        self,
        composite: float,
        n_critical_missing: int,
        n_critical_weak: int,
        experience_meets: bool,
    ) -> Recommendation:
        """Apply rule-based tiering.

        Hard rules (applied first):
          - 2+ critical skills missing → no_hire regardless of score
          - 1 critical skill missing AND score < hire threshold → consider ceiling
          - 3+ critical weak/missing → consider ceiling

        Score thresholds (applied after hard rules):
          - >= 78 → strong_hire
          - >= 62 → hire
          - >= 42 → consider
          - < 42  → no_hire
        """
        # Hard rule 1: multiple critical misses → no_hire
        if n_critical_missing >= 2:
            logger.debug(f"DECISION | Hard rule: {n_critical_missing} critical misses → no_hire")
            return Recommendation.NO_HIRE

        # Hard rule 2: 1 critical miss + experience gap → consider ceiling
        total_critical_issues = n_critical_missing + n_critical_weak
        if total_critical_issues >= 3:
            logger.debug(f"DECISION | Hard rule: {total_critical_issues} critical issues → consider ceiling")
            return Recommendation.CONSIDER if composite >= THRESHOLD_CONSIDER else Recommendation.NO_HIRE

        # Hard rule 3: 1 critical missing → cap at consider unless score is very high
        if n_critical_missing == 1 and composite < THRESHOLD_HIRE:
            logger.debug(f"DECISION | Hard rule: 1 critical missing, score {composite:.1f} → consider ceiling")
            return Recommendation.CONSIDER

        # Score thresholds
        if composite >= THRESHOLD_STRONG_HIRE:
            return Recommendation.STRONG_HIRE
        elif composite >= THRESHOLD_HIRE:
            return Recommendation.HIRE
        elif composite >= THRESHOLD_CONSIDER:
            return Recommendation.CONSIDER
        else:
            return Recommendation.NO_HIRE

    def _evidence_density(self, skill_matches: list[SkillMatchResult]) -> float:
        """Fraction of non-missing skills that have evidence."""
        non_missing = [sm for sm in skill_matches if sm.match_level != MatchLevel.MISSING]
        if not non_missing:
            return 0.1 if skill_matches else 0.5
        with_evidence = sum(1 for sm in non_missing if sm.evidence.strip())
        return with_evidence / len(non_missing)

    def _signal_consistency(self, skill_matches: list[SkillMatchResult]) -> float:
        """Measures how consistent the signal is.

        High consistency: candidate is uniformly good or bad.
        Low consistency: mixed signals (e.g., expert at some critical skills, missing others).
        """
        if not skill_matches:
            return 0.5

        SCORE_MAP = {
            MatchLevel.STRONG: 1.0,
            MatchLevel.PARTIAL: 0.6,
            MatchLevel.WEAK: 0.25,
            MatchLevel.MISSING: 0.0,
        }
        scores = [SCORE_MAP[sm.match_level] for sm in skill_matches]
        if len(scores) == 1:
            return 0.7

        mean = sum(scores) / len(scores)
        variance = sum((s - mean) ** 2 for s in scores) / len(scores)
        # Normalize: variance in [0, 0.25] (max for binary 0/1)
        # High variance = low consistency
        consistency = max(0.0, 1.0 - (variance / 0.25))
        return consistency

    def _gap_severity_score(self, gaps: list[GapEntry]) -> float:
        """Score from 0 (many critical gaps) to 1 (no gaps)."""
        if not gaps:
            return 1.0

        PENALTY = {
            GapSeverity.CRITICAL: 0.25,
            GapSeverity.IMPORTANT: 0.10,
            GapSeverity.MINOR: 0.03,
        }
        total_penalty = sum(PENALTY.get(g.severity, 0.05) for g in gaps)
        return max(0.0, 1.0 - total_penalty)

    def _generate_explanation(
        self,
        recommendation: Recommendation,
        composite: float,
        skill_matches: list[SkillMatchResult],
        experience: ExperienceAssessment,
        gaps: list[GapEntry],
        strengths: list[StrengthEntry],
        jd: ParsedJobDescription,
    ) -> str:
        """Generate a signal-derived explanation (no free-form LLM)."""
        parts = []

        # Opening: recommendation + score
        rec_label = {
            Recommendation.STRONG_HIRE: "a strong hire",
            Recommendation.HIRE: "a hire",
            Recommendation.CONSIDER: "a borderline candidate",
            Recommendation.NO_HIRE: "not recommended for this role",
        }[recommendation]
        parts.append(
            f"Based on signal analysis, this candidate is {rec_label} "
            f"(composite score {composite:.0f}/100)."
        )

        # Skill coverage
        strong = sum(1 for sm in skill_matches if sm.match_level == MatchLevel.STRONG)
        total = len(skill_matches)
        if total > 0:
            parts.append(
                f"They match {strong}/{total} required skills strongly."
            )

        # Top strength
        if strengths:
            top = strengths[0]
            parts.append(f"Key strength: {top.description}.")

        # Critical gaps (if any)
        critical_gaps = [g for g in gaps if g.severity == GapSeverity.CRITICAL]
        if critical_gaps:
            gap_names = ", ".join(g.skill for g in critical_gaps[:2])
            parts.append(
                f"Critical gaps identified: {gap_names} — "
                f"these are required for the {jd.title} role."
            )

        # Experience
        if experience.years_match == YearsMatch.EXCEEDS:
            parts.append(
                f"Experience exceeds requirements "
                f"({experience.years_candidate:.0f} vs "
                f"{experience.years_required_min or 0:.0f}+ years required)."
            )
        elif experience.years_match == YearsMatch.BELOW:
            parts.append(
                f"Experience is below requirements "
                f"({experience.years_candidate or '?'} vs "
                f"{experience.years_required_min or '?'}+ years required)."
            )

        return " ".join(parts)
