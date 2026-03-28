"""
Pure-Python manual trace of the HireAI signal-driven pipeline.
No pydantic required — implements the same algorithms in stdlib.

Verifies:
  1. Ontology alias resolution
  2. Deterministic skill matching (direct → implied → same-parent → missing)
  3. Evidence guarantee enforcement
  4. Experience / education assessment
  5. Decision agent tiering and confidence calibration
  6. Final validation

Two candidates tested:
  A) John Kim — strong ML/DS candidate (6 yrs)
  B) Alice Johnson — weak match (2 yrs frontend)
"""

import math, json


# ══════════════════════════════════════════════════════════════════════════════
# Minimal reproductions of ontology constants (subset for this test)
# ══════════════════════════════════════════════════════════════════════════════

ALIAS_MAP = {
    "nlp": "Natural Language Processing",
    "ml": "Machine Learning",
    "tf": "Terraform",       # NOTE: "tf" maps to Terraform, NOT TensorFlow
    "tf2": "TensorFlow",
    "pytorch": "PyTorch",
    "tensorflow": "TensorFlow",
    "sklearn": "Scikit-learn",
    "scikit-learn": "Scikit-learn",
    "scikit": "Scikit-learn",
    "py": "Python",
    "aws": "AWS",
    "k8s": "Kubernetes",
    "rag": "RAG",
    "embeddings": "Embeddings",
    "sql": "SQL",
    "communication": "Communication",
    "machine learning": "Machine Learning",
    "natural language processing": "Natural Language Processing",
    "deep learning": "Deep Learning",
    "dl": "Deep Learning",
    "pyspark": "Spark",
    "pandas": "Pandas",
    "numpy": "NumPy",
}

CANONICAL_SKILLS = {
    "Python", "JavaScript", "TypeScript", "Java", "Go", "Rust",
    "SQL", "NoSQL", "PostgreSQL", "MySQL", "MongoDB",
    "AWS", "Google Cloud Platform", "Azure",
    "Machine Learning", "Deep Learning", "Natural Language Processing",
    "TensorFlow", "PyTorch", "Scikit-learn", "Pandas", "NumPy",
    "Docker", "Kubernetes", "Terraform", "CI/CD",
    "Communication", "Leadership", "Mentoring", "Collaboration",
    "RAG", "LLM", "Embeddings", "Vector Databases",
    "Spark", "Airflow", "ETL",
}

PARENT_MAP = {
    "Python": "programming",
    "JavaScript": "programming",
    "TypeScript": "programming",
    "Java": "programming",
    "Go": "programming",
    "Rust": "programming",
    "SQL": "database",
    "NoSQL": "database",
    "PostgreSQL": "database",
    "MySQL": "database",
    "MongoDB": "database",
    "AWS": "cloud",
    "Google Cloud Platform": "cloud",
    "Azure": "cloud",
    "Machine Learning": "ml",
    "Deep Learning": "ml",
    "Natural Language Processing": "ml",
    "TensorFlow": "ml",
    "PyTorch": "ml",
    "Scikit-learn": "ml",
    "RAG": "ml",
    "LLM": "ml",
    "Embeddings": "ml",
    "Vector Databases": "ml",
    "Docker": "devops",
    "Kubernetes": "devops",
    "Terraform": "devops",
    "CI/CD": "devops",
    "Communication": "soft",
    "Leadership": "soft",
    "Mentoring": "soft",
    "Collaboration": "soft",
    "Spark": "data",
    "Airflow": "data",
    "ETL": "data",
    "Pandas": "data",
    "NumPy": "data",
}

IMPLICATION_MAP = {
    "PyTorch":    ["Machine Learning", "Deep Learning"],
    "TensorFlow": ["Machine Learning", "Deep Learning"],
    "Scikit-learn": ["Machine Learning"],
    "RAG": ["LLM", "Embeddings", "Vector Databases"],
    "Kubernetes": ["Docker"],
    "Django": ["Python"],
    "Flask": ["Python"],
    "FastAPI": ["Python"],
}


def canonicalize(name):
    """Mirror of ontology.canonicalize()."""
    import re
    norm = re.sub(r"\s+", " ", name.strip().lower())
    # 1. Exact match
    for c in CANONICAL_SKILLS:
        if c.lower() == norm:
            return c
    # 2. Alias
    if norm in ALIAS_MAP:
        return ALIAS_MAP[norm]
    # 3. Partial alias
    for alias, canonical in ALIAS_MAP.items():
        if alias in norm or norm in alias:
            return canonical
    # 4. Canonical substring
    for c in sorted(CANONICAL_SKILLS, key=len, reverse=True):
        if c.lower() in norm:
            return c
    return name.strip().title()


def get_parent(skill):
    return PARENT_MAP.get(skill)


def get_implied(skill):
    return IMPLICATION_MAP.get(skill, [])


def skills_share_parent(a, b):
    pa, pb = PARENT_MAP.get(a), PARENT_MAP.get(b)
    return pa is not None and pa == pb


# ══════════════════════════════════════════════════════════════════════════════
# Enums (as strings for this trace)
# ══════════════════════════════════════════════════════════════════════════════

CRITICAL   = "critical"
IMPORTANT  = "important"
SECONDARY  = "secondary"
STRONG     = "strong"
PARTIAL    = "partial"
WEAK       = "weak"
MISSING    = "missing"
EXCEEDS    = "exceeds"
MEETS      = "meets"
BELOW      = "below"
UNKNOWN    = "unknown"
HIGH       = "high"
MEDIUM     = "medium"
LOW        = "low"

MATCH_SCORE_MAP = {STRONG: 100.0, PARTIAL: 60.0, WEAK: 25.0, MISSING: 0.0}
IMPORTANCE_W    = {CRITICAL: 3.0, IMPORTANT: 1.5, SECONDARY: 0.5}
MATCH_SCORE_01  = {STRONG: 1.0, PARTIAL: 0.6, WEAK: 0.25, MISSING: 0.0}
IMPORTANCE_W_01 = {CRITICAL: 1.0, IMPORTANT: 0.6, SECONDARY: 0.3}
PROFICIENCY_SCORE = {"expert": 1.0, "advanced": 0.85, "intermediate": 0.65, "beginner": 0.35}
PROFICIENCY_MATCH = {"expert": STRONG, "advanced": STRONG, "intermediate": PARTIAL, "beginner": WEAK}

SEVERITY_PENALTY = {CRITICAL: 0.25, IMPORTANT: 0.10, "minor": 0.03}

THRESHOLD_STRONG_HIRE = 78.0
THRESHOLD_HIRE = 62.0
THRESHOLD_MAYBE = 42.0


# ══════════════════════════════════════════════════════════════════════════════
# JD: Senior Data Scientist – AI/ML Platform
# required_skills: [(canonical_name, importance), ...]
# ══════════════════════════════════════════════════════════════════════════════

JD = {
    "title": "Senior Data Scientist",
    "experience_min_years": 5.0,
    "education_min_level": "bachelor",
    "education_preferred_fields": ["Computer Science", "Statistics", "Mathematics"],
    "preferred_areas": [],  # no area restrictions
    "required_skills": [
        # (canonical_name, importance)
        ("Python",                      CRITICAL),
        ("Machine Learning",            CRITICAL),
        ("PyTorch",                     CRITICAL),
        ("TensorFlow",                  IMPORTANT),
        ("Scikit-learn",                IMPORTANT),
        ("Natural Language Processing", CRITICAL),
        ("SQL",                         IMPORTANT),
        ("AWS",                         IMPORTANT),
        ("Communication",               SECONDARY),
    ],
}


# ══════════════════════════════════════════════════════════════════════════════
# Candidate A — John Kim (strong match)
# resume_skills: [(canonical_name, proficiency, evidence), ...]
# ══════════════════════════════════════════════════════════════════════════════

RESUME_JOHN = {
    "name": "John Kim",
    "total_years": 6.0,
    "skills": [
        ("Python",                      "advanced", "Python extensively used across all roles"),
        ("Machine Learning",            "advanced", "6 years experience in ML and data science"),
        ("PyTorch",                     "advanced", "Built NLP pipelines using PyTorch and Hugging Face transformers"),
        ("TensorFlow",                  "advanced", "Trained TensorFlow models for image classification"),
        ("Scikit-learn",                "advanced", "Used scikit-learn for feature engineering and model selection"),
        ("Natural Language Processing", "advanced", "Built NLP pipelines using transformers, embeddings, and RAG"),
        ("SQL",                         "advanced", "Wrote complex SQL queries for data pipelines and reporting"),
        ("AWS",                         "advanced", "Deployed models to AWS SageMaker; used S3 and EC2"),
        ("Communication",               "intermediate", "Mentored 3 junior data scientists; presented to stakeholders"),
        ("Docker",                      "intermediate", "Docker used for model serving containers"),
        ("Deep Learning",               "advanced", "Deep learning models for CV and NLP"),
    ],
    "experience": [
        ("Senior ML Engineer", "DataCorp",  4.0),
        ("Data Scientist",     "StartupAI", 2.0),
    ],
    "education": [("master", "Computer Science", "Stanford University")],
}


# ══════════════════════════════════════════════════════════════════════════════
# Candidate B — Alice Johnson (weak match)
# ══════════════════════════════════════════════════════════════════════════════

RESUME_ALICE = {
    "name": "Alice Johnson",
    "total_years": 2.0,
    "skills": [
        ("Python", "intermediate", "Occasionally used Python for automation scripts"),
        ("SQL",    "intermediate", "Basic SQL queries for reporting in frontend role"),
    ],
    "experience": [
        ("Frontend Developer", "WebAgency", 2.0),
    ],
    "education": [("bachelor", "Information Technology", "State University")],
}


# ══════════════════════════════════════════════════════════════════════════════
# Matching engine (mirrors matching_engine.py logic exactly)
# ══════════════════════════════════════════════════════════════════════════════

def match_skills(jd, resume):
    """Returns list of skill match dicts."""
    candidate_skills = {s[0]: s for s in resume["skills"]}  # canonical → (canonical, proficiency, evidence)
    results = []

    for (req_canonical, importance) in jd["required_skills"]:
        # 1. Direct match
        if req_canonical in candidate_skills:
            _, proficiency, evidence = candidate_skills[req_canonical]
            match_level = PROFICIENCY_MATCH.get(proficiency, PARTIAL)
            proficiency_factor = PROFICIENCY_SCORE.get(proficiency, 0.65)
            base_score = MATCH_SCORE_01[match_level] * proficiency_factor
            skill_score = min(base_score * IMPORTANCE_W_01[importance], 1.0)
            matched_by = f"Direct match ({proficiency})"
        else:
            # 2. Implied-by check
            implied_match = None
            implied_evidence = ""
            for cand_skill, cand_proficiency, cand_evidence in resume["skills"]:
                if req_canonical in get_implied(cand_skill):
                    implied_match = cand_skill
                    implied_evidence = cand_evidence or f"{cand_skill} implies {req_canonical}"
                    break

            if implied_match:
                match_level = PARTIAL
                evidence = implied_evidence
                skill_score = MATCH_SCORE_01[PARTIAL] * IMPORTANCE_W_01[importance]
                matched_by = f"Implied by {implied_match}"
            else:
                # 3. Same parent category
                req_parent = get_parent(req_canonical)
                parent_match = None
                parent_evidence = ""
                if req_parent:
                    for cand_skill, cand_prof, cand_evidence in resume["skills"]:
                        if get_parent(cand_skill) == req_parent:
                            parent_match = cand_skill
                            parent_evidence = cand_evidence or f"{cand_skill} in same category ({req_parent})"
                            break

                if parent_match:
                    match_level = WEAK
                    evidence = parent_evidence
                    skill_score = MATCH_SCORE_01[WEAK] * IMPORTANCE_W_01[importance]
                    matched_by = f"Same parent '{req_parent}': {parent_match}"
                else:
                    match_level = MISSING
                    evidence = ""
                    skill_score = 0.0
                    matched_by = "No match found"

            proficiency = None

        # Evidence guarantee (mirrors evaluate_validator.enforce_evidence_guarantees)
        # But note: the model_validator in SkillMatchResult also downgrades on creation
        if match_level in (STRONG, PARTIAL) and not evidence.strip():
            match_level = WEAK
            skill_score = min(skill_score, MATCH_SCORE_01[WEAK] * IMPORTANCE_W_01[importance])
            matched_by = f"[evidence-downgraded] {matched_by}"

        results.append({
            "skill": req_canonical,
            "importance": importance,
            "match_level": match_level,
            "evidence": evidence,
            "reason": matched_by,
            "skill_score": round(skill_score, 3),
        })

    return results


def compute_skills_score(skill_matches):
    total_weight = 0.0
    weighted_sum = 0.0
    for sm in skill_matches:
        w = IMPORTANCE_W[sm["importance"]]
        s = MATCH_SCORE_MAP[sm["match_level"]]
        weighted_sum += s * w
        total_weight += w
    return weighted_sum / total_weight if total_weight > 0 else 50.0


def assess_experience(jd, resume):
    yc = resume["total_years"]
    yr = jd["experience_min_years"]
    if yc is not None and yr is not None:
        if yc >= yr * 1.2:
            years_match = EXCEEDS
        elif yc >= yr:
            years_match = MEETS
        else:
            years_match = BELOW
    else:
        years_match = UNKNOWN

    # preferred_areas empty → MEDIUM relevance (has experience)
    relevance = MEDIUM if resume["experience"] else UNKNOWN

    base = {EXCEEDS: 90.0, MEETS: 75.0, BELOW: 35.0, UNKNOWN: 60.0}[years_match]
    adj  = {HIGH: 5.0, MEDIUM: 0.0, LOW: -10.0, UNKNOWN: 0.0}[relevance]
    score = max(0.0, min(100.0, base + adj))
    meets = years_match in (MEETS, EXCEEDS, UNKNOWN)

    # Evidence from experience entries
    parts = [f"{t} at {c}" for (t, c, _) in resume["experience"][:3]]
    evidence = "; ".join(parts)

    return {
        "years_candidate": yc,
        "years_required": yr,
        "years_match": years_match,
        "relevance": relevance,
        "meets_requirements": meets,
        "score": score,
        "evidence": evidence,
    }


EDUCATION_LEVELS = {"none": 0, "high school": 1, "associate": 2, "bachelor": 3, "master": 4, "phd": 5}

def assess_education(jd, resume):
    if not resume["education"]:
        return {"meets_requirements": False, "level_match": UNKNOWN, "field_relevance": UNKNOWN, "evidence": "", "score": 40.0}

    highest = None
    highest_level = -1
    for (degree, field, institution) in resume["education"]:
        for ln, lv in EDUCATION_LEVELS.items():
            if ln in degree.lower() and lv > highest_level:
                highest_level = lv
                highest = (degree, field, institution)

    required_level = EDUCATION_LEVELS.get(jd["education_min_level"].lower(), 0)

    if highest_level == -1:
        level_match = UNKNOWN
    elif highest_level > required_level:
        level_match = EXCEEDS
    elif highest_level >= required_level:
        level_match = MEETS
    else:
        level_match = BELOW

    field_relevance = UNKNOWN
    if jd["education_preferred_fields"] and highest:
        field_lower = highest[1].lower()
        for pf in jd["education_preferred_fields"]:
            if pf.lower() in field_lower or field_lower in pf.lower():
                field_relevance = HIGH
                break
        if field_relevance == UNKNOWN:
            field_relevance = LOW
    elif highest:
        field_relevance = MEDIUM

    base = {EXCEEDS: 95.0, MEETS: 80.0, BELOW: 40.0, UNKNOWN: 60.0}[level_match]
    adj  = {HIGH: 5.0, MEDIUM: 0.0, LOW: -5.0, UNKNOWN: 0.0}[field_relevance]
    score = max(0.0, min(100.0, base + adj))
    meets = level_match in (MEETS, EXCEEDS, UNKNOWN)

    evidence = " — ".join([p for p in (highest[0], highest[1], highest[2]) if p]) if highest else ""

    return {
        "level_match": level_match,
        "field_relevance": field_relevance,
        "meets_requirements": meets,
        "score": score,
        "evidence": evidence,
    }


def build_gaps(skill_matches, experience):
    gaps = []
    sev_order = {CRITICAL: 0, IMPORTANT: 1, "minor": 2}
    for sm in skill_matches:
        ml, imp = sm["match_level"], sm["importance"]
        if ml == MISSING:
            if imp == CRITICAL:
                sev, impact = CRITICAL, "Critical requirement not met — directly blocks role performance"
            elif imp == IMPORTANT:
                sev, impact = IMPORTANT, "Significant gap — will require ramp-up time"
            else:
                sev, impact = "minor", "Nice-to-have not present"
            gaps.append({"skill": sm["skill"], "severity": sev, "impact": impact, "description": f"{sm['skill']} not found in resume"})
        elif ml == WEAK:
            if imp == CRITICAL:
                sev, impact = CRITICAL, "Critical skill only weakly evidenced — significant risk"
            elif imp == IMPORTANT:
                sev, impact = IMPORTANT, "Proficiency level uncertain — verify in interview"
            else:
                sev, impact = "minor", "Limited evidence of proficiency"
            gaps.append({"skill": sm["skill"], "severity": sev, "impact": impact, "description": f"{sm['skill']} weakly evidenced"})

    # Experience gap
    if experience["years_match"] == BELOW and experience["years_required"]:
        yc = experience["years_candidate"] or 0
        yr = experience["years_required"]
        delta = yr - yc
        sev = CRITICAL if delta > 2 else IMPORTANT
        gaps.append({"skill": "Experience", "severity": sev,
                     "description": f"~{yc:.0f} years vs {yr:.0f}+ required",
                     "impact": f"Under-experienced by ~{delta:.0f} years"})

    gaps.sort(key=lambda g: sev_order.get(g["severity"], 2))
    return gaps


def build_strengths(skill_matches, experience):
    strengths = []
    strong_critical = [sm for sm in skill_matches if sm["match_level"] == STRONG and sm["importance"] == CRITICAL and sm["evidence"].strip()]
    for sm in strong_critical[:3]:
        strengths.append({"description": f"Strong {sm['skill']} proficiency (critical requirement met)", "evidence": sm["evidence"], "skill": sm["skill"]})

    strong_important = [sm for sm in skill_matches if sm["match_level"] == STRONG and sm["importance"] == IMPORTANT and sm["evidence"].strip() and sm not in strong_critical]
    for sm in strong_important[:2]:
        strengths.append({"description": f"Demonstrated {sm['skill']} experience", "evidence": sm["evidence"], "skill": sm["skill"]})

    if experience["years_match"] == EXCEEDS and experience["evidence"]:
        yc = experience["years_candidate"]
        yr = experience["years_required"]
        strengths.append({"description": f"Experience exceeds requirements ({yc:.0f} vs {yr:.0f}+ years)", "evidence": experience["evidence"]})

    return strengths[:6]


def compute_overall_fit(skill_matches, experience):
    total = len(skill_matches)
    if total == 0: return 50.0
    matched = sum(1 for sm in skill_matches if sm["match_level"] != MISSING)
    breadth = (matched / total) * 100.0
    if experience["years_match"] == EXCEEDS:
        breadth = min(100.0, breadth + 10.0)
    return breadth


def evidence_density(skill_matches):
    non_missing = [sm for sm in skill_matches if sm["match_level"] != MISSING]
    if not non_missing:
        return 0.1 if skill_matches else 0.5
    with_ev = sum(1 for sm in non_missing if sm["evidence"].strip())
    return with_ev / len(non_missing)


def signal_consistency(skill_matches):
    if not skill_matches: return 0.5
    scores = [MATCH_SCORE_01[sm["match_level"]] for sm in skill_matches]
    if len(scores) == 1: return 0.7
    mean = sum(scores) / len(scores)
    variance = sum((s - mean) ** 2 for s in scores) / len(scores)
    return max(0.0, 1.0 - (variance / 0.25))


def gap_severity_score(gaps):
    if not gaps: return 1.0
    penalty = sum(SEVERITY_PENALTY.get(g["severity"], 0.05) for g in gaps)
    return max(0.0, 1.0 - penalty)


def tier_recommendation(composite, n_critical_missing, n_critical_weak, experience_meets):
    if n_critical_missing >= 2:
        return "no_hire", "Hard rule: 2+ critical missing → no_hire"
    total_critical_issues = n_critical_missing + n_critical_weak
    if total_critical_issues >= 3:
        return ("maybe" if composite >= THRESHOLD_MAYBE else "no_hire"), f"Hard rule: {total_critical_issues} critical issues → maybe ceiling"
    if n_critical_missing == 1 and composite < THRESHOLD_HIRE:
        return "maybe", "Hard rule: 1 critical missing, score below hire threshold → maybe ceiling"
    if composite >= THRESHOLD_STRONG_HIRE: return "strong_hire", "Score threshold"
    if composite >= THRESHOLD_HIRE:       return "hire",        "Score threshold"
    if composite >= THRESHOLD_MAYBE:      return "maybe",       "Score threshold"
    return "no_hire", "Score threshold"


def build_suggested_actions(gaps, skill_matches, recommendation):
    actions = []
    for g in gaps:
        if g["severity"] == CRITICAL and g["skill"] != "Experience":
            actions.append(f"Probe depth of {g['skill']} knowledge — this is a critical requirement")
    partial_important = [sm for sm in skill_matches if sm["match_level"] in (PARTIAL, WEAK) and sm["importance"] == IMPORTANT]
    for sm in partial_important[:2]:
        actions.append(f"Assess {sm['skill']} proficiency level in technical screen")
    exp_gap = next((g for g in gaps if g["skill"] == "Experience"), None)
    if exp_gap:
        if exp_gap["severity"] == CRITICAL:
            actions.append("Clarify actual years of relevant experience — may be understated in resume")
        else:
            actions.append("Discuss pace of progression and depth of experience in interview")
    if recommendation in ("no_hire", "maybe") and not actions:
        actions.append("Consider for a more junior role or with additional skill development plan")
    if recommendation == "strong_hire" and not actions:
        actions.append("Recommend for technical interview — strong signal across all requirements")
    return actions[:5]


# ══════════════════════════════════════════════════════════════════════════════
# Full pipeline trace
# ══════════════════════════════════════════════════════════════════════════════

def run_pipeline(jd, resume):
    name = resume["name"]
    print(f"\n{'═'*68}")
    print(f"  CANDIDATE: {name}")
    print(f"{'═'*68}")

    # D4 — matching
    sm = match_skills(jd, resume)
    exp = assess_experience(jd, resume)
    edu = assess_education(jd, resume)

    print(f"\n[D4] Skill matching:")
    icon = {STRONG: "✅", PARTIAL: "🔶", WEAK: "⚠️ ", MISSING: "❌"}
    imp_tag = {CRITICAL: "[CRIT]", IMPORTANT: "[IMP] ", SECONDARY: "[SEC] "}
    for s in sm:
        ev = s["evidence"][:55].replace("\n"," ") if s["evidence"] else "(no evidence)"
        print(f"     {icon.get(s['match_level'],'?')} {imp_tag[s['importance']]} {s['skill']:<28} score={s['skill_score']:.3f}  ev: {ev}")

    counts = {}
    for s in sm:
        counts[s["match_level"]] = counts.get(s["match_level"], 0) + 1
    print(f"     Summary: {json.dumps(counts)}")

    print(f"\n[D4] Experience: {exp['years_match']}  meets={exp['meets_requirements']}  score={exp['score']:.1f}")
    print(f"     {exp['evidence']}")
    print(f"[D4] Education:  level={edu['level_match']}  field_rel={edu['field_relevance']}  score={edu['score']:.1f}")
    print(f"     {edu['evidence']}")

    gaps = build_gaps(sm, exp)
    strengths = build_strengths(sm, exp)

    sev_icon = {CRITICAL: "🔴", IMPORTANT: "🟠", "minor": "🟡"}
    print(f"\n[D4] Gaps ({len(gaps)}):  ", end="")
    for g in gaps: print(f"{sev_icon.get(g['severity'],'⚪')}{g['skill']}", end="  ")
    print()
    print(f"[D4] Strengths ({len(strengths)}):")
    for s in strengths:
        ev = s["evidence"][:60] if s["evidence"] else "(none)"
        print(f"     → {s['description']}")
        print(f"       ev: {ev}")

    # D5 — decision
    skills_score   = compute_skills_score(sm)
    overall_fit    = compute_overall_fit(sm, exp)
    composite      = (skills_score*0.40 + exp["score"]*0.30 + edu["score"]*0.15 + overall_fit*0.15)

    n_crit_missing = sum(1 for s in sm if s["match_level"] == MISSING and s["importance"] == CRITICAL)
    n_crit_weak    = sum(1 for s in sm if s["match_level"] == WEAK    and s["importance"] == CRITICAL)

    recommendation, tier_reason = tier_recommendation(composite, n_crit_missing, n_crit_weak, exp["meets_requirements"])
    suggested = build_suggested_actions(gaps, sm, recommendation)

    ed  = evidence_density(sm)
    sc  = signal_consistency(sm)
    gss = gap_severity_score(gaps)
    raw_conf = ed*0.4 + sc*0.35 + gss*0.25
    confidence = max(0.05, min(0.97, raw_conf))

    print(f"\n[D5] Skills score:    {skills_score:.1f} / 100")
    print(f"[D5] Overall fit:     {overall_fit:.1f} / 100")
    print(f"[D5] Composite:       {composite:.1f} / 100")
    print(f"[D5] Tiering:         {tier_reason}")
    print(f"[D5] Recommendation:  {recommendation.upper()}")
    print(f"[D5] Confidence:      {confidence:.3f}  (ev_density={ed:.3f}, consistency={sc:.3f}, gap_sev={gss:.3f})")
    print(f"[D5] Crit missing:    {n_crit_missing}  |  Crit weak: {n_crit_weak}")
    has_crit_gaps = any(g["severity"] == CRITICAL for g in gaps)
    print(f"[D5] Has crit gaps:   {has_crit_gaps}")
    if has_crit_gaps:
        print(f"[D5] Crit gaps:       {[g['skill'] for g in gaps if g['severity']==CRITICAL]}")

    print(f"\n[D5] Decision trace:")
    strong_n = sum(1 for s in sm if s["match_level"] == STRONG)
    missing_n = sum(1 for s in sm if s["match_level"] == MISSING)
    print(f"     Step 1 [skill_match]     (w=40%) → {skills_score:.0f}/100 ({n_crit_missing} crit missing, {n_crit_weak} crit weak)")
    print(f"     Step 2 [experience]      (w=30%) → {exp['years_match']} ({exp['years_candidate']}y vs {exp['years_required']}+ req) → score {exp['score']:.0f}/100")
    print(f"     Step 3 [education]       (w=15%) → {edu['level_match']} / field {edu['field_relevance']} → score {edu['score']:.0f}/100")
    print(f"     Step 4 [overall_fit]     (w=15%) → {overall_fit:.0f}/100")
    print(f"     Step 5 [composite_score]      → {skills_score:.0f}×0.40 + {exp['score']:.0f}×0.30 + {edu['score']:.0f}×0.15 + {overall_fit:.0f}×0.15 = {composite:.1f}")
    if has_crit_gaps:
        crit_names = ", ".join(g["skill"] for g in gaps if g["severity"]==CRITICAL)
        print(f"     Step 6 [critical_gap_check] → {len([g for g in gaps if g['severity']==CRITICAL])} critical gap(s): {crit_names}")
    step_n = 7 if has_crit_gaps else 6
    print(f"     Step {step_n} [recommendation]    → {recommendation} (score={composite:.1f}, crit_miss={n_crit_missing})")

    print(f"\n[D5] Explanation:")
    strong = sum(1 for s in sm if s["match_level"] == STRONG)
    total = len(sm)
    rec_label = {"strong_hire": "a strong hire", "hire": "a hire", "maybe": "a borderline candidate", "no_hire": "not recommended for this role"}[recommendation]
    explanation = f"Based on signal analysis, this candidate is {rec_label} (composite score {composite:.0f}/100)."
    explanation += f" They match {strong}/{total} required skills strongly."
    if strengths:
        explanation += f" Key strength: {strengths[0]['description']}."
    crit_gaps = [g for g in gaps if g["severity"] == CRITICAL]
    if crit_gaps:
        g_names = ", ".join(g["skill"] for g in crit_gaps[:2])
        explanation += f" Critical gaps: {g_names}."
    if exp["years_match"] == EXCEEDS:
        explanation += f" Experience exceeds requirements ({exp['years_candidate']:.0f} vs {exp['years_required']:.0f}+ years)."
    elif exp["years_match"] == BELOW:
        explanation += f" Experience is below requirements ({exp['years_candidate']} vs {exp['years_required']}+ years)."
    print(f"     {explanation}")

    # D6 — validation
    print(f"\n[D6] Validation:")
    errors = []
    required_in_output = {s[0] for s in jd["required_skills"]}
    output_skills = {s["skill"] for s in sm}
    missing_from_out = required_in_output - output_skills
    if missing_from_out: errors.append(f"Missing skill matches: {missing_from_out}")
    strong_no_ev = [s for s in sm if s["match_level"] == STRONG and not s["evidence"].strip()]
    if strong_no_ev: errors.append(f"Strong matches without evidence: {[s['skill'] for s in strong_no_ev]}")
    if not (0 <= confidence <= 1): errors.append(f"Confidence out of range: {confidence}")
    if not (0 <= composite <= 100): errors.append(f"Composite out of range: {composite}")
    if recommendation in ("strong_hire", "hire") and has_crit_gaps:
        print(f"     ⚠️  WARNING: Positive recommendation with critical gaps")
    if errors:
        print(f"     ✗ FAIL: {errors}")
    else:
        print(f"     ✓ PASS — all checks satisfied")

    print(f"\n[D6] Suggested actions:")
    for a in suggested:
        print(f"     → {a}")

    return {
        "name": name,
        "recommendation": recommendation,
        "composite": composite,
        "confidence": confidence,
        "evidence_density": ed,
        "signal_consistency": sc,
        "gap_severity_score": gss,
        "has_critical_gaps": has_crit_gaps,
        "critical_gap_count": len([g for g in gaps if g["severity"]==CRITICAL]),
        "strength_count": len(strengths),
        "gap_count": len(gaps),
        "n_crit_missing": n_crit_missing,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════════════

print("\n" + "█"*68)
print("  HireAI Signal-Driven Pipeline — End-to-End Trace")
print("  JD: Senior Data Scientist – AI/ML Platform")
print("█"*68)

print(f"\n{'─'*68}")
print("  ONTOLOGY CHECK — alias resolution")
print(f"{'─'*68}")
tests = [
    ("python",   "Python"),
    ("pytorch",  "PyTorch"),
    ("tensorflow","TensorFlow"),
    ("sklearn",  "Scikit-learn"),
    ("nlp",      "Natural Language Processing"),
    ("sql",      "SQL"),
    ("aws",      "AWS"),
    ("k8s",      "Kubernetes"),
    ("rag",      "RAG"),
    ("ml",       "Machine Learning"),
    ("tf",       "Terraform"),  # tf → Terraform (NOT TensorFlow)
]
all_ok = True
for raw, expected in tests:
    got = canonicalize(raw)
    ok  = "✓" if got == expected else "✗"
    if got != expected: all_ok = False
    print(f"  {ok}  '{raw}' → '{got}'  (expected '{expected}')")
print(f"\n  Ontology: {'ALL PASS ✓' if all_ok else 'FAILURES DETECTED ✗'}")

r_john  = run_pipeline(JD, RESUME_JOHN)
r_alice = run_pipeline(JD, RESUME_ALICE)

print(f"\n{'═'*68}")
print("  COMPARISON SUMMARY")
print(f"{'═'*68}")
print(f"  {'Metric':<30} {'John Kim':>14} {'Alice Johnson':>14}")
print(f"  {'─'*58}")
rows = [
    ("Recommendation",       r_john["recommendation"],                  r_alice["recommendation"]),
    ("Composite Score",      f"{r_john['composite']:.1f}",              f"{r_alice['composite']:.1f}"),
    ("Confidence",           f"{r_john['confidence']:.3f}",             f"{r_alice['confidence']:.3f}"),
    ("Evidence Density",     f"{r_john['evidence_density']:.3f}",       f"{r_alice['evidence_density']:.3f}"),
    ("Signal Consistency",   f"{r_john['signal_consistency']:.3f}",     f"{r_alice['signal_consistency']:.3f}"),
    ("Gap Severity Score",   f"{r_john['gap_severity_score']:.3f}",     f"{r_alice['gap_severity_score']:.3f}"),
    ("Has Critical Gaps",    str(r_john["has_critical_gaps"]),          str(r_alice["has_critical_gaps"])),
    ("Critical Gap Count",   str(r_john["critical_gap_count"]),         str(r_alice["critical_gap_count"])),
    ("Critical Missing",     str(r_john["n_crit_missing"]),             str(r_alice["n_crit_missing"])),
    ("Strength Count",       str(r_john["strength_count"]),             str(r_alice["strength_count"])),
    ("Gap Count",            str(r_john["gap_count"]),                  str(r_alice["gap_count"])),
]
for label, a, b in rows:
    print(f"  {label:<30} {a:>14} {b:>14}")
print()
