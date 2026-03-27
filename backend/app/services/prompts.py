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
            "name": "concrete skill or technology name",
            "importance": "critical|important|secondary",
            "category": "technical|soft|domain",
            "capability_label": "original capability area phrase from JD (or null)"
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

CRITICAL INSTRUCTION — Skill Decomposition:
Many JDs list high-level capability areas (e.g. "Agent Architecture & Engineering",
"AI Platform Integration", "Cloud-Native Engineering"). These are NOT matchable skills.
You MUST decompose each capability area into its concrete constituent skills/technologies.

Examples of correct decomposition:
  "Agent Architecture & Engineering"  ->  LangChain, RAG pipelines, Vector Databases,
                                          LLM Observability, Agentic Frameworks
  "AI Platform Integration"           ->  OpenAI API, LLM, Prompt Engineering,
                                          Machine Learning, REST API
  "Cloud-Native Engineering"          ->  AWS, Docker, Kubernetes, CI/CD, Microservices
  "Client Engagement"                 ->  Communication, Stakeholder Management, Presentation Skills
  "Knowledge Sharing"                 ->  Mentoring, Technical Writing, Documentation
  "Measure & Improve"                 ->  A/B Testing, Data Analysis, Product Analytics,
                                          Metrics-Driven Development
  "Domain-Specific Workflows"         ->  Domain knowledge (healthcare/finance/etc.), Process Design,
                                          Requirements Analysis

Rules:
- Each entry in required_skills must be a CONCRETE skill, technology, or competency
  — never a multi-word section heading or capability phrase
- Set capability_label to the original section heading from the JD so skills can be
  grouped visually (e.g. all "Agent Architecture & Engineering" skills together)
- Classify importance based on how central the capability is to the role:
  critical = core to role, important = strongly preferred, secondary = nice-to-have
- Extract ALL constituent skills from provided text only. DO NOT hallucinate.

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
            "category": "technical|soft|domain",
            "proficiency": "expert|advanced|intermediate|beginner",
            "evidence": "brief evidence from resume"
        }}
    ],
    "experience": [
        {{
            "title": "job title",
            "company": "company name",
            "duration": "approximate duration",
            "role_summary": "brief summary of the role",
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

CRITICAL INSTRUCTIONS for skill extraction:

1. PARSE STRUCTURED SKILL LISTS.
   Resumes often list skills in structured sections like:
     "AI Infrastructure: LangChain, LlamaIndex, Vector DBs"
     "Cloud: AWS, Kubernetes, Docker"
     "Languages: Python, TypeScript, Go"
   You MUST extract EACH comma-separated item as a separate skill entry.
   Do not skip these sections — they are the most information-dense part of the resume.

2. EMIT ONE ENTRY PER SKILL.
   Never merge multiple skills into one entry (e.g. do NOT output "LangChain, LlamaIndex").
   Each skill must have its own JSON object in the skills array.

3. INFER PROFICIENCY FROM CONTEXT.
   - If the skill is listed under "Core Expertise" or "Expert in" → expert/advanced
   - If used in multiple job roles → advanced
   - If mentioned once in passing → intermediate
   - For skills with no context, default to intermediate

4. EVIDENCE = WHERE/HOW USED.
   For skills mentioned in job descriptions, quote or summarize the usage context.
   For skills in skill lists without context, set evidence to the section name
   (e.g. "Listed under Core AI Infrastructure").

5. DO NOT HALLUCINATE. Only extract skills explicitly mentioned in the resume.

Respond with ONLY the JSON object. No other text."""



# ── Semantic Enrichment (D4b) ────────────────────────────────────────────────

SEMANTIC_ENRICHMENT_PROMPT = """You are evaluating specific skill matches between a job requirement and a candidate resume.

For each item below, determine whether the candidate's resume provides evidence of competency
in the required skill — even if the skill is named differently or expressed through related work.

SKILLS TO EVALUATE:
{skills_to_evaluate}

CANDIDATE RESUME CONTEXT:
{resume_context}

Return a JSON array with one object per item, in the same order as the input list:
[
  {{
    "index": 0,
    "required_skill": "exact skill name from input",
    "demonstrates_competency": true or false,
    "suggested_match_level": "strong|partial|weak|missing",
    "confidence": 0.0 to 1.0,
    "reasoning": "one concise sentence citing specific resume evidence"
  }}
]

Scoring rules:
- "strong"  = candidate clearly demonstrates this skill with concrete, named examples
- "partial" = candidate likely has the skill based on closely related work or implied usage
- "weak"    = marginal or indirect evidence; possible but uncertain
- "missing" = no evidence of this skill — do not infer from unrelated work

Confidence rules:
- 0.85+  = you are highly confident in your assessment
- 0.60–0.84 = reasonably confident
- 0.40–0.59 = uncertain — err toward weak/missing
- Below 0.40 = very uncertain — set suggested_match_level to "missing"

Critical constraints:
- DO NOT hallucinate. Only use information explicitly present in the resume context provided.
- DO NOT upgrade a match purely because of job title. Require actual skill evidence.
- When in doubt, prefer a lower match level over a higher one.
- reasoning must reference specific text from the resume (tool names, project descriptions, etc.)

Respond with ONLY the JSON array. No other text."""


# ── Full Evaluation ──────────────────────────────────────────────────────────

EVALUATION_PROMPT = """You are evaluating a candidate for a specific role. Analyze the match carefully.

JOB REQUIREMENTS:
{job_requirements}

CANDIDATE PROFILE:
{candidate_profile}

Perform a thorough evaluation and return a JSON object with this exact structure:
{{
    "recommendation": "strong_hire|hire|consider|no_hire",
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
9. DO NOT hallucinate. Only use the information provided in the resume and job description.

Respond with ONLY the JSON object. No other text."""
