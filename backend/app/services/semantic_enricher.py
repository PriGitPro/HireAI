"""Semantic Enrichment Layer — D4b hybrid LLM enrichment for uncertain skill matches.

Sits between the deterministic matching engine (D4) and the Decision Agent (D5).
It is called ONLY for matches the rule engine left uncertain:

  Eligible for enrichment:
    - MISSING  on critical or important skills  (ontology gap or synonym miss)
    - WEAK     on critical or important skills  (could be upgraded with semantic evidence)
    - PARTIAL  on critical skills only          (could be confirmed as STRONG)

  Never re-evaluated:
    - STRONG matches of any kind               (rule engine is authoritative)
    - Any match on secondary skills            (low signal-to-noise, not worth the call)

Design invariants:
  - Single batch LLM call per evaluation (not N individual calls)
  - Results cached by (required_skill, resume_evidence_hash) — free on repeat evals
  - LLM can only UPGRADE uncertain matches, never DOWNGRADE confirmed rule results
  - Source tracked in match_reason: "rule_based" | "llm_enriched"
  - Graceful fallback: any LLM failure leaves the rule-based result unchanged
  - Min confidence threshold (config.SEMANTIC_ENRICHMENT_MIN_CONFIDENCE) gates upgrades
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Optional

from app.core.config import settings
from app.services.pipeline_schemas import (
    MatchLevel,
    ParsedResume,
    SkillImportance,
    SkillMatchResult,
)
from app.services.prompts import SEMANTIC_ENRICHMENT_PROMPT, SYSTEM_PROMPT

logger = logging.getLogger("hireai.enricher")

# ── Match level ordering (used to prevent downgrades) ────────────────────────

_LEVEL_RANK: dict[MatchLevel, int] = {
    MatchLevel.STRONG:  3,
    MatchLevel.PARTIAL: 2,
    MatchLevel.WEAK:    1,
    MatchLevel.MISSING: 0,
}

_RANK_TO_LEVEL: dict[int, MatchLevel] = {v: k for k, v in _LEVEL_RANK.items()}

_STR_TO_LEVEL: dict[str, MatchLevel] = {
    "strong":  MatchLevel.STRONG,
    "partial": MatchLevel.PARTIAL,
    "weak":    MatchLevel.WEAK,
    "missing": MatchLevel.MISSING,
}


# ── Internal result type ──────────────────────────────────────────────────────

@dataclass
class EnrichmentResult:
    """LLM assessment for a single uncertain skill match."""
    required_skill: str
    demonstrates_competency: bool
    suggested_match_level: MatchLevel
    confidence: float
    reasoning: str
    from_cache: bool = False


# ── Eligibility logic ─────────────────────────────────────────────────────────

def _is_eligible(match: SkillMatchResult) -> bool:
    """Determine if a match should be sent for LLM enrichment.

    Eligible = uncertain result on a skill that matters enough to warrant
    the extra LLM call. STRONG matches are always excluded.
    """
    level = match.match_level
    importance = match.importance

    if level == MatchLevel.STRONG:
        return False  # Rule engine is authoritative — never re-evaluate

    if importance == SkillImportance.SECONDARY:
        return False  # Not worth the LLM call for nice-to-haves

    # MISSING or WEAK on critical/important → always enrich
    if level in (MatchLevel.MISSING, MatchLevel.WEAK):
        return True

    # PARTIAL on critical only → try to confirm as STRONG
    if level == MatchLevel.PARTIAL and importance == SkillImportance.CRITICAL:
        return True

    return False


# ── Cache key ─────────────────────────────────────────────────────────────────

def _cache_key(required_skill: str, resume_skills_text: str) -> str:
    """Stable cache key: (canonical skill name, hash of resume skill evidence)."""
    payload = f"{required_skill.lower()}|{resume_skills_text}"
    return hashlib.md5(payload.encode()).hexdigest()[:16]


# ── Resume context builder ────────────────────────────────────────────────────

def _build_resume_context(resume: ParsedResume) -> str:
    """Produce a compact, LLM-readable summary of the candidate's resume.

    Includes:
      - Professional summary (if present)
      - All skills with proficiency + evidence
      - Experience role titles and highlights (top 3 entries)
    """
    lines: list[str] = []

    if resume.summary:
        lines.append(f"SUMMARY: {resume.summary}")
        lines.append("")

    if resume.skills:
        lines.append("SKILLS:")
        for s in resume.skills:
            ev = f" — {s.evidence}" if s.evidence else ""
            lines.append(f"  • {s.name} [{s.proficiency}]{ev}")
        lines.append("")

    if resume.experience:
        lines.append("EXPERIENCE (top roles):")
        for exp in resume.experience[:3]:
            title_line = f"  {exp.title}" + (f" @ {exp.company}" if exp.company else "")
            if exp.duration:
                title_line += f" ({exp.duration})"
            lines.append(title_line)
            for h in exp.highlights[:3]:
                lines.append(f"    - {h}")
        lines.append("")

    if resume.notable_achievements:
        lines.append("NOTABLE ACHIEVEMENTS:")
        for a in resume.notable_achievements[:3]:
            lines.append(f"  • {a}")

    return "\n".join(lines)


# ── Skills-to-evaluate payload builder ───────────────────────────────────────

def _build_skills_payload(
    matches: list[SkillMatchResult],
    resume: ParsedResume,
) -> str:
    """Format uncertain matches as a JSON array for the enrichment prompt."""
    items = []
    for i, m in enumerate(matches):
        # Include what the rule engine found — gives the LLM useful context
        related_info = ""
        if m.matched_skill and m.matched_skill != m.required_skill:
            ev = f" (evidence: {m.evidence})" if m.evidence else ""
            related_info = f"{m.matched_skill}{ev}"

        item = {
            "index": i,
            "required_skill": m.required_skill,
            "current_rule_result": m.match_level.value,
            "importance": m.importance.value,
        }
        if related_info:
            item["candidate_has_related"] = related_info

        items.append(item)

    return json.dumps(items, indent=2)


# ── SemanticEnricher ──────────────────────────────────────────────────────────

class SemanticEnricher:
    """Batch LLM enrichment for uncertain D4 skill matches.

    Usage:
        enricher = SemanticEnricher(llm_provider)
        enriched_matches = await enricher.enrich(all_matches, resume)
    """

    def __init__(self, llm):
        self._llm = llm
        # In-process cache: (skill, evidence_hash) → EnrichmentResult
        # Shared across all evaluations in the same process lifetime.
        self._cache: dict[str, EnrichmentResult] = {}

    async def enrich(
        self,
        skill_matches: list[SkillMatchResult],
        resume: ParsedResume,
    ) -> list[SkillMatchResult]:
        """Run hybrid enrichment on a full match list.

        Steps:
          1. Identify eligible (uncertain) matches
          2. Split into cache-hit and cache-miss sets
          3. Batch-call LLM for cache misses (single call)
          4. Store results in cache
          5. Merge enriched results back, applying upgrades only
          6. Return updated match list (STRONG matches untouched)
        """
        eligible = [m for m in skill_matches if _is_eligible(m)]

        if not eligible:
            logger.debug("D4b.enricher | No eligible matches — skipping LLM call")
            return skill_matches

        # Cap batch size per config
        if len(eligible) > settings.SEMANTIC_ENRICHMENT_MAX_BATCH:
            logger.info(
                f"D4b.enricher | Capping batch from {len(eligible)}"
                f" to {settings.SEMANTIC_ENRICHMENT_MAX_BATCH}"
            )
            eligible = eligible[: settings.SEMANTIC_ENRICHMENT_MAX_BATCH]

        resume_context = _build_resume_context(resume)
        resume_skills_text = " ".join(
            f"{s.canonical_name}:{s.evidence}" for s in resume.skills
        )

        # Partition into cache hits and misses
        cache_hits: dict[str, EnrichmentResult] = {}
        to_enrich: list[SkillMatchResult] = []

        for m in eligible:
            key = _cache_key(m.required_skill, resume_skills_text)
            if key in self._cache:
                cached = self._cache[key]
                cache_hits[m.required_skill] = cached
            else:
                to_enrich.append(m)

        logger.info(
            f"D4b.enricher | eligible={len(eligible)}"
            f" | cache_hits={len(cache_hits)}"
            f" | llm_batch={len(to_enrich)}"
        )

        # LLM batch call for cache misses
        llm_results: list[EnrichmentResult] = []
        if to_enrich:
            llm_results = await self._call_llm_batch(to_enrich, resume_context, resume_skills_text)

        # Build lookup: required_skill → EnrichmentResult
        enrichment_map: dict[str, EnrichmentResult] = {**cache_hits}
        for r in llm_results:
            enrichment_map[r.required_skill] = r

        # Merge: apply upgrades, track source
        return self._merge(skill_matches, enrichment_map)

    # ── LLM batch call ────────────────────────────────────────────────────────

    async def _call_llm_batch(
        self,
        matches: list[SkillMatchResult],
        resume_context: str,
        resume_skills_text: str,
    ) -> list[EnrichmentResult]:
        """Execute a single LLM call for all uncertain matches.

        Returns a list of EnrichmentResults in the same order as `matches`.
        On any failure, returns an empty list so rule-based results are preserved.
        """
        skills_payload = _build_skills_payload(matches, None)

        prompt = SEMANTIC_ENRICHMENT_PROMPT.format(
            skills_to_evaluate=skills_payload,
            resume_context=resume_context,
        )

        logger.info(
            f"D4b.enricher | LLM batch call"
            f" | skills={len(matches)}"
            f" | prompt_chars={len(prompt)}"
        )

        try:
            response = await asyncio.wait_for(
                self._llm.generate(
                    prompt=prompt,
                    system_prompt=SYSTEM_PROMPT,
                    temperature=0.0,   # Deterministic — no creativity wanted here
                    force_json=True,
                ),
                timeout=settings.LLM_TIMEOUT,
            )

            raw = response.as_json()

            # The response can be a top-level list OR {"results": [...]}
            if isinstance(raw, list):
                items = raw
            elif isinstance(raw, dict):
                # Try common wrapper keys
                items = raw.get("results") or raw.get("items") or raw.get("skills") or []
            else:
                logger.warning("D4b.enricher | Unexpected LLM response shape — skipping enrichment")
                return []

            results = self._parse_llm_response(items, matches, resume_skills_text)
            logger.info(
                f"D4b.enricher | LLM call complete"
                f" | parsed={len(results)}/{len(matches)}"
                f" | latency={response.latency_ms}ms"
            )
            return results

        except asyncio.TimeoutError:
            logger.warning(
                f"D4b.enricher | LLM timeout after {settings.LLM_TIMEOUT}s"
                " — falling back to rule-based results"
            )
            return []
        except Exception as e:
            logger.warning(
                f"D4b.enricher | LLM call failed: {type(e).__name__}: {e}"
                " — falling back to rule-based results"
            )
            return []

    # ── Response parser ───────────────────────────────────────────────────────

    def _parse_llm_response(
        self,
        items: list,
        original_matches: list[SkillMatchResult],
        resume_skills_text: str,
    ) -> list[EnrichmentResult]:
        """Parse the LLM JSON array into EnrichmentResult objects.

        Uses index-based alignment as the primary lookup and falls back to
        skill-name matching for robustness against out-of-order LLM responses.
        """
        # Build index → original match lookup
        by_index: dict[int, SkillMatchResult] = {i: m for i, m in enumerate(original_matches)}
        by_skill: dict[str, SkillMatchResult] = {m.required_skill.lower(): m for m in original_matches}

        results: list[EnrichmentResult] = []

        for item in items:
            if not isinstance(item, dict):
                continue

            # Resolve which original match this item refers to
            idx = item.get("index")
            skill_name = (item.get("required_skill") or "").strip()
            original = by_index.get(idx) or by_skill.get(skill_name.lower())

            if not original:
                logger.debug(f"D4b.enricher | Could not match LLM item to original: {item}")
                continue

            # Parse fields with safe defaults
            demonstrates = bool(item.get("demonstrates_competency", False))
            level_str = (item.get("suggested_match_level") or "missing").lower()
            suggested_level = _STR_TO_LEVEL.get(level_str, MatchLevel.MISSING)
            confidence = float(item.get("confidence") or 0.0)
            reasoning = str(item.get("reasoning") or "")

            result = EnrichmentResult(
                required_skill=original.required_skill,
                demonstrates_competency=demonstrates,
                suggested_match_level=suggested_level,
                confidence=confidence,
                reasoning=reasoning,
                from_cache=False,
            )

            # Store in cache
            key = _cache_key(original.required_skill, resume_skills_text)
            self._cache[key] = result
            results.append(result)

        return results

    # ── Merge logic ───────────────────────────────────────────────────────────

    def _merge(
        self,
        original_matches: list[SkillMatchResult],
        enrichment_map: dict[str, EnrichmentResult],
    ) -> list[SkillMatchResult]:
        """Apply enrichment upgrades to the original match list.

        Rules:
          - STRONG matches: never modified
          - Enrichment confidence < MIN_CONFIDENCE: no change
          - LLM suggests lower level than rule: no downgrade
          - LLM confirms or upgrades: apply new level + append reasoning to match_reason
          - match_reason always records the enrichment source for auditability
        """
        updated: list[SkillMatchResult] = []

        for match in original_matches:
            enrichment = enrichment_map.get(match.required_skill)

            if enrichment is None or match.match_level == MatchLevel.STRONG:
                # Not enriched or already STRONG — keep as-is
                updated.append(match)
                continue

            if enrichment.confidence < settings.SEMANTIC_ENRICHMENT_MIN_CONFIDENCE:
                # LLM was not confident enough — preserve rule-based result
                logger.debug(
                    f"D4b.merge | {match.required_skill}"
                    f" | LLM confidence {enrichment.confidence:.2f} < threshold"
                    f" — rule result kept ({match.match_level.value})"
                )
                updated.append(match)
                continue

            original_rank = _LEVEL_RANK[match.match_level]
            suggested_rank = _LEVEL_RANK[enrichment.suggested_match_level]

            if suggested_rank <= original_rank:
                # LLM agrees with or is more pessimistic than rule engine
                # No downgrade — preserve rule result but annotate with reasoning
                new_reason = (
                    f"{match.match_reason} | llm_enriched (confidence={enrichment.confidence:.2f},"
                    f" confirms={enrichment.suggested_match_level.value}): {enrichment.reasoning}"
                )
                updated.append(match.model_copy(update={"match_reason": new_reason}))
                logger.debug(
                    f"D4b.merge | {match.required_skill}"
                    f" | LLM confirms {match.match_level.value} — no change"
                )
                continue

            # LLM suggests an upgrade — apply it
            new_level = enrichment.suggested_match_level
            new_score = _upgraded_score(new_level, match)
            new_reason = (
                f"llm_enriched from {match.match_level.value}"
                f" (confidence={enrichment.confidence:.2f}): {enrichment.reasoning}"
            )

            upgraded = match.model_copy(update={
                "match_level": new_level,
                "skill_score": new_score,
                "match_reason": new_reason,
                # Preserve evidence if it exists; use reasoning as fallback
                "evidence": match.evidence or enrichment.reasoning,
            })

            logger.info(
                f"D4b.merge | UPGRADE | {match.required_skill}"
                f" | {match.match_level.value} → {new_level.value}"
                f" | confidence={enrichment.confidence:.2f}"
                f" | from_cache={enrichment.from_cache}"
            )
            updated.append(upgraded)

        upgrades = sum(
            1 for o, u in zip(original_matches, updated)
            if o.match_level != u.match_level
        )
        logger.info(
            f"D4b.merge | DONE"
            f" | total={len(updated)}"
            f" | upgraded={upgrades}"
            f" | unchanged={len(updated) - upgrades}"
        )
        return updated


# ── Score helper ──────────────────────────────────────────────────────────────

def _upgraded_score(new_level: MatchLevel, original: SkillMatchResult) -> float:
    """Recompute skill_score for an upgraded match level.

    Preserves the importance weighting from the original match.
    """
    from app.services.matching_engine import IMPORTANCE_WEIGHT, MATCH_SCORE

    base = MATCH_SCORE[new_level]
    weight = IMPORTANCE_WEIGHT[original.importance]
    return min(base * weight, 1.0)
