"""Prompt templates for the AI evaluation pipeline.

All prompts return structured JSON to ensure deterministic, parseable outputs.
"""

# ── System Prompt ────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are an expert hiring evaluation assistant. Your role is to:
1. Analyze candidate resumes against job requirements
2. Provide evidence-based assessments
3. Generate structured, actionable hiring recommendations

Core principles:
- Every claim must be backed by evidence from the resume or job description
- Be fair and objective — do not use protected attributes (age, gender, race, etc.)
- When uncertain, express lower confidence rather than guessing
- Provide actionable insights, not just analysis

CRITICAL: You MUST respond with ONLY valid JSON. No explanatory text before or after the JSON.
Do NOT use markdown code fences. Do NOT add any commentary outside the JSON object.
Your entire response must be a single parseable JSON object."""


# ── Job Description Parsing ──────────────────────────────────────────────────

JD_PARSING_PROMPT = """Analyze the following job description and extract structured information.

JOB DESCRIPTION:
{job_description}

Return a JSON object with this exact structure:
{{
    "title": "extracted job title",
    "summary": "2-3 sentence summary of the role",
    "required_skills": [
        {{
            "name": "skill name",
            "importance": "critical|important|secondary",
            "category": "technical|soft|domain"
        }}
    ],
    "experience_requirements": {{
        "min_years": null or number,
        "max_years": null or number,
        "preferred_areas": ["area1", "area2"],
        "description": "brief description"
    }},
    "education_requirements": {{
        "min_level": "bachelor|master|phd|none",
        "preferred_fields": ["field1", "field2"],
        "description": "brief description"
    }},
    "key_responsibilities": ["resp1", "resp2"],
    "nice_to_haves": ["item1", "item2"]
}}

Important:
- Classify skills by importance: critical (must-have), important (strongly preferred), secondary (nice-to-have)
- Be precise about experience requirements
- Extract ALL relevant skills mentioned

Respond with ONLY the JSON object. No other text."""


# ── Resume Parsing ───────────────────────────────────────────────────────────

RESUME_PARSING_PROMPT = """Analyze the following resume and extract structured information.

RESUME TEXT:
{resume_text}

Return a JSON object with this exact structure:
{{
    "name": "candidate name",
    "email": "email if found",
    "phone": "phone if found",
    "summary": "2-3 sentence professional summary",
    "skills": [
        {{
            "name": "skill name",
            "proficiency": "expert|advanced|intermediate|beginner",
            "evidence": "brief evidence from resume"
        }}
    ],
    "experience": [
        {{
            "title": "job title",
            "company": "company name",
            "duration": "approximate duration",
            "highlights": ["key achievement 1", "key achievement 2"]
        }}
    ],
    "total_experience_years": null or number,
    "education": [
        {{
            "degree": "degree type",
            "field": "field of study",
            "institution": "school name",
            "year": "graduation year or null"
        }}
    ],
    "certifications": ["cert1", "cert2"],
    "notable_achievements": ["achievement1", "achievement2"]
}}

Important:
- Extract skills with evidence of actual usage, not just listing
- Estimate total years of experience from work history
- Note any quantified achievements

Respond with ONLY the JSON object. No other text."""


# ── Full Evaluation ──────────────────────────────────────────────────────────

EVALUATION_PROMPT = """You are evaluating a candidate for a specific role. Analyze the match carefully.

JOB REQUIREMENTS:
{job_requirements}

CANDIDATE PROFILE:
{candidate_profile}

Perform a thorough evaluation and return a JSON object with this exact structure:
{{
    "recommendation": "strong_hire|hire|maybe|no_hire",
    "confidence": 0.0 to 1.0,
    "composite_score": 0 to 100,
    "skill_matches": [
        {{
            "skill": "skill name",
            "match_level": "strong|partial|weak|missing",
            "evidence": "specific evidence from resume",
            "importance": "critical|important|secondary"
        }}
    ],
    "experience_assessment": {{
        "meets_requirements": true/false,
        "years_match": "exceeds|meets|below",
        "relevance": "high|medium|low",
        "evidence": "specific evidence",
        "score": 0 to 100
    }},
    "education_assessment": {{
        "meets_requirements": true/false,
        "level_match": "exceeds|meets|below",
        "field_relevance": "high|medium|low",
        "evidence": "specific evidence",
        "score": 0 to 100
    }},
    "strengths": ["strength1 with evidence", "strength2 with evidence"],
    "gaps": ["gap1 — impact description", "gap2 — impact description"],
    "explanation": "3-5 sentence human-readable explanation of the overall assessment and recommendation",
    "decision_trace": [
        {{
            "step": 1,
            "action": "what was evaluated",
            "finding": "what was found",
            "impact": "positive|negative|neutral"
        }}
    ],
    "suggested_actions": [
        "Specific actionable next step 1",
        "Specific actionable next step 2"
    ]
}}

EVALUATION RULES:
1. Every skill_match must have evidence from the resume. If no evidence, mark as "missing".
2. Score critical skill matches higher — a missing critical skill should significantly lower the composite score.
3. Confidence should reflect how much evidence you have. Limited resume info = lower confidence.
4. The composite_score should weight: skills (40%), experience (30%), education (15%), overall fit (15%).
5. Be calibrated: a strong candidate should score 75-95. A weak candidate 20-45. Don't compress scores.
6. suggested_actions should be specific and actionable (e.g., "Probe depth of Python experience in technical interview").
7. decision_trace should show your reasoning steps in order.
8. Explanation must reference specific evidence, not generic statements.

Respond with ONLY the JSON object. No other text."""
