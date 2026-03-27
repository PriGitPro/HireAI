"""Skill Ontology — canonical skill names, alias resolution, parent inference.

All skill comparison in the matching engine must go through this module to
ensure consistent naming across parsing → matching → UI output.

Design:
  - CANONICAL_SKILLS: the single source of truth for skill names
  - ALIAS_MAP: maps common abbreviations / variants → canonical name
  - PARENT_MAP: maps canonical skill → its parent category
  - RELATED_MAP: skills that imply partial knowledge of another
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Optional


# ── Canonical skill registry ──────────────────────────────────────────────────
# Format: "Canonical Name" (title-cased, human-readable)

CANONICAL_SKILLS: set[str] = {
    # Programming languages
    "Python", "JavaScript", "TypeScript", "Java", "Go", "Rust", "C", "C++",
    "C#", "Ruby", "PHP", "Swift", "Kotlin", "Scala", "R", "MATLAB",
    "Bash", "Shell Scripting", "PowerShell",

    # Web frameworks / frontend
    "React", "Next.js", "Vue.js", "Angular", "Svelte", "HTML", "CSS",
    "Tailwind CSS", "SASS", "Redux", "GraphQL", "REST API",

    # Backend frameworks
    "FastAPI", "Django", "Flask", "Express.js", "Spring Boot", "Rails",
    "Node.js", "NestJS",

    # Databases
    "PostgreSQL", "MySQL", "SQLite", "MongoDB", "Redis", "Elasticsearch",
    "Cassandra", "DynamoDB", "Oracle", "SQL", "NoSQL",

    # Cloud & DevOps
    "AWS", "Google Cloud Platform", "Azure", "Docker", "Kubernetes",
    "Terraform", "Ansible", "CI/CD", "Jenkins", "GitHub Actions",
    "GitLab CI", "Helm", "Linux", "Unix",

    # Data & ML
    "Machine Learning", "Deep Learning", "Natural Language Processing",
    "Computer Vision", "TensorFlow", "PyTorch", "Scikit-learn", "Pandas",
    "NumPy", "Spark", "Hadoop", "Airflow", "Data Engineering",
    "Data Analysis", "Statistics", "A/B Testing",
    "Data Visualization", "Product Analytics",

    # LLM / AI (modern) — tools
    "LLM", "Prompt Engineering", "RAG", "OpenAI API", "Langchain", "LlamaIndex",
    "Vector Databases", "Embeddings",

    # LLM / AI — capability-level canonicals (what JDs actually ask for)
    "Agentic Frameworks",      # LangChain, LlamaIndex, AutoGen, CrewAI etc.
    "LLM Observability",       # LangSmith, Langfuse, tracing
    "AI Product Development",  # end-to-end ML product lifecycle
    "Fine-Tuning",             # model adaptation
    "Model Evaluation",        # evals, benchmarking, model accuracy, LLM evals
    "Data Quality",            # data validation, data integrity, data cleaning

    # Architecture & practices
    "Microservices", "Event-Driven Architecture", "Domain-Driven Design",
    "System Design", "API Design", "Distributed Systems",
    "Agile", "Scrum", "Kanban", "Test-Driven Development",
    "Unit Testing", "Integration Testing",

    # Tools & platforms
    "Git", "GitHub", "GitLab", "Jira", "Confluence",
    "Kafka", "RabbitMQ", "gRPC", "WebSockets",

    # Security
    "OAuth", "JWT", "Security Best Practices", "OWASP",

    # Soft skills
    "Communication", "Leadership", "Mentoring", "Problem Solving",
    "Collaboration", "Project Management",
    "Stakeholder Management", "Presentation Skills", "Technical Writing",
    "Documentation", "Cross-Functional Collaboration",

    # Process & methodology
    "Metrics-Driven Development", "Requirements Analysis", "Process Design",

    # Integration / Enterprise
    "API Integration", "ETL", "Data Pipelines", "Webhooks",
    "iPaaS", "Salesforce", "SAP", "Workday",
}


# ── Alias map ─────────────────────────────────────────────────────────────────
# Maps: lower-case-normalized alias → canonical name

ALIAS_MAP: dict[str, str] = {
    # JavaScript variants
    "js": "JavaScript",
    "javascript": "JavaScript",
    "es6": "JavaScript",
    "es2015": "JavaScript",
    "vanillajs": "JavaScript",
    "ts": "TypeScript",
    "typescript": "TypeScript",

    # Python
    "py": "Python",
    "python3": "Python",
    "python 3": "Python",

    # Go
    "golang": "Go",

    # C++
    "cpp": "C++",
    "c plus plus": "C++",
    "cplusplus": "C++",

    # C#
    "csharp": "C#",
    "c sharp": "C#",
    "dotnet": "C#",
    ".net": "C#",

    # Java
    "java": "Java",
    "jvm": "Java",

    # Frontend
    "reactjs": "React",
    "react.js": "React",
    "react js": "React",
    "nextjs": "Next.js",
    "next js": "Next.js",
    "vuejs": "Vue.js",
    "vue js": "Vue.js",
    "vue": "Vue.js",
    "angularjs": "Angular",
    "ng": "Angular",
    "html5": "HTML",
    "css3": "CSS",
    "tailwind": "Tailwind CSS",
    "sass": "SASS",
    "scss": "SASS",

    # Backend
    "fastapi": "FastAPI",
    "fast api": "FastAPI",
    "django": "Django",
    "flask": "Flask",
    "express": "Express.js",
    "expressjs": "Express.js",
    "springboot": "Spring Boot",
    "spring": "Spring Boot",
    "node": "Node.js",
    "nodejs": "Node.js",
    "node.js": "Node.js",

    # Databases
    "postgres": "PostgreSQL",
    "postgresql": "PostgreSQL",
    "psql": "PostgreSQL",
    "mysql": "MySQL",
    "mongo": "MongoDB",
    "mongodb": "MongoDB",
    "elastic": "Elasticsearch",
    "elasticsearch": "Elasticsearch",
    "dynamo": "DynamoDB",
    "dynamodb": "DynamoDB",
    "sqlite": "SQLite",

    # Cloud
    "aws": "AWS",
    "amazon web services": "AWS",
    "gcp": "Google Cloud Platform",
    "google cloud": "Google Cloud Platform",
    "google cloud platform": "Google Cloud Platform",
    "azure": "Azure",
    "microsoft azure": "Azure",

    # DevOps
    "k8s": "Kubernetes",
    "kubernetes": "Kubernetes",
    "docker": "Docker",
    "terraform": "Terraform",
    "tf": "Terraform",
    "ci/cd": "CI/CD",
    "cicd": "CI/CD",
    "github actions": "GitHub Actions",
    "gitlab ci": "GitLab CI",
    "gitlab-ci": "GitLab CI",

    # ML/Data
    "ml": "Machine Learning",
    "machine learning": "Machine Learning",
    "ml models": "Machine Learning",
    "ml pipelines": "Machine Learning",
    "model training": "Machine Learning",
    "model training pipelines": "Machine Learning",  # JD uses this exact phrase
    "training pipelines": "Machine Learning",
    "ml engineering": "Machine Learning",
    "applied ml": "Machine Learning",
    "applied machine learning": "Machine Learning",
    "dl": "Deep Learning",
    "deep learning": "Deep Learning",
    "neural networks": "Deep Learning",
    "neural network": "Deep Learning",
    "nlp": "Natural Language Processing",
    "natural language processing": "Natural Language Processing",
    "text processing": "Natural Language Processing",
    "cv": "Computer Vision",
    "computer vision": "Computer Vision",
    "tensorflow": "TensorFlow",
    "tf2": "TensorFlow",
    "pytorch": "PyTorch",
    "torch": "PyTorch",
    "sklearn": "Scikit-learn",
    "scikit": "Scikit-learn",
    "scikit-learn": "Scikit-learn",
    "pandas": "Pandas",
    "numpy": "NumPy",
    "apache spark": "Spark",
    "pyspark": "Spark",
    "airflow": "Airflow",
    "apache airflow": "Airflow",

    # Statistics — plural/variant forms
    "statistics": "Statistics",
    "stats": "Statistics",
    "statistical analysis": "Statistics",
    "statistical modeling": "Statistics",
    "statistical modelling": "Statistics",
    "statistical methods": "Statistics",
    "probability": "Statistics",
    "probability and statistics": "Statistics",
    "quantitative analysis": "Statistics",
    "bayesian": "Statistics",
    "regression analysis": "Statistics",

    # Data Analysis — variant forms
    "data analysis": "Data Analysis",
    "data analytics": "Data Analysis",
    "analytics": "Data Analysis",
    "business analytics": "Data Analysis",
    "translating complex model metrics": "Data Analysis",  # JD uses this exact phrase
    "translating metrics": "Data Analysis",
    "interpreting model results": "Data Analysis",
    "model interpretation": "Data Analysis",

    # Architecture
    "microservice": "Microservices",
    "micro-services": "Microservices",
    "ddd": "Domain-Driven Design",
    "domain driven design": "Domain-Driven Design",
    "tdd": "Test-Driven Development",
    "test driven development": "Test-Driven Development",
    "api design": "API Design",
    "rest": "REST API",
    "restful": "REST API",
    "restful api": "REST API",
    "graphql": "GraphQL",

    # Messaging
    "kafka": "Kafka",
    "apache kafka": "Kafka",
    "rabbitmq": "RabbitMQ",
    "rabbit mq": "RabbitMQ",
    "grpc": "gRPC",
    "websocket": "WebSockets",
    "websockets": "WebSockets",

    # Tools
    "git": "Git",
    "github": "GitHub",
    "gitlab": "GitLab",
    "jira": "Jira",

    # Security
    "oauth": "OAuth",
    "oauth2": "OAuth",
    "jwt": "JWT",
    "json web token": "JWT",
    "owasp": "OWASP",

    # LLM / AI
    "llm": "LLM",
    "llms": "LLM",                                  # plural form
    "large language model": "LLM",
    "large language models": "LLM",
    "foundation models": "LLM",
    "foundation model": "LLM",
    "prompt engineering": "Prompt Engineering",
    "prompting": "Prompt Engineering",
    "prompt design": "Prompt Engineering",
    "rag": "RAG",
    "retrieval augmented generation": "RAG",
    "retrieval-augmented generation": "RAG",
    "retrieval augmented": "RAG",
    "langchain": "Langchain",
    "lang chain": "Langchain",
    "llamaindex": "LlamaIndex",
    "llama index": "LlamaIndex",
    "llama-index": "LlamaIndex",
    "openai": "OpenAI API",
    "chatgpt": "OpenAI API",
    "gpt-4": "OpenAI API",
    "gpt4": "OpenAI API",
    "gpt-3": "OpenAI API",
    "vector db": "Vector Databases",
    "vector database": "Vector Databases",
    "vector store": "Vector Databases",
    "pinecone": "Vector Databases",  # specific product → capability
    "chroma": "Vector Databases",
    "weaviate": "Vector Databases",
    "faiss": "Vector Databases",
    "qdrant": "Vector Databases",
    "embeddings": "Embeddings",
    "text embeddings": "Embeddings",
    "agentic": "Agentic Frameworks",
    "agent framework": "Agentic Frameworks",
    "agentic framework": "Agentic Frameworks",
    "agentic ai": "Agentic Frameworks",
    "agentic orchestration": "Agentic Frameworks",  # JD uses this exact phrase
    "agent orchestration": "Agentic Frameworks",
    "multi-agent orchestration": "Agentic Frameworks",
    "multi agent orchestration": "Agentic Frameworks",
    "multi-agent systems": "Agentic Frameworks",
    "multi agent systems": "Agentic Frameworks",
    "multi-agent": "Agentic Frameworks",
    "autogen": "Agentic Frameworks",
    "crewai": "Agentic Frameworks",
    "llm agents": "Agentic Frameworks",
    "ai agents": "Agentic Frameworks",
    "llm observability": "LLM Observability",
    "langsmith": "LLM Observability",
    "langfuse": "LLM Observability",
    "model monitoring": "LLM Observability",
    "ai product development": "AI Product Development",
    "ml product development": "AI Product Development",
    "fine tuning": "Fine-Tuning",
    "fine-tuning": "Fine-Tuning",
    "finetuning": "Fine-Tuning",
    "rlhf": "Fine-Tuning",
    "instruction tuning": "Fine-Tuning",

    # Model Evaluation (evals) — JDs often use these exact terms
    "evals": "Model Evaluation",
    "eval": "Model Evaluation",
    "model evaluation": "Model Evaluation",
    "model evaluations": "Model Evaluation",
    "model accuracy": "Model Evaluation",
    "model performance": "Model Evaluation",
    "llm evals": "Model Evaluation",
    "llm evaluation": "Model Evaluation",
    "llm evaluations": "Model Evaluation",
    "benchmarking": "Model Evaluation",
    "model benchmarking": "Model Evaluation",
    "evaluation frameworks": "Model Evaluation",
    "eval frameworks": "Model Evaluation",
    "model testing": "Model Evaluation",
    "model assessment": "Model Evaluation",

    # Data Quality — JDs and resumes use these variants
    "data quality": "Data Quality",
    "data validation": "Data Quality",
    "data integrity": "Data Quality",
    "data cleaning": "Data Quality",
    "data cleansing": "Data Quality",
    "data governance": "Data Quality",

    # Soft skills (new)
    "stakeholder management": "Stakeholder Management",
    "presentation skills": "Presentation Skills",
    "presentations": "Presentation Skills",
    "technical writing": "Technical Writing",
    "documentation": "Documentation",
    "cross-functional": "Cross-Functional Collaboration",
    "cross functional": "Cross-Functional Collaboration",

    # Process
    "metrics driven": "Metrics-Driven Development",
    "metrics-driven": "Metrics-Driven Development",
    "requirements analysis": "Requirements Analysis",
    "product analytics": "Product Analytics",
    "data visualization": "Data Visualization",
    "dataviz": "Data Visualization",

    # Soft skills
    "communication": "Communication",
    "leadership": "Leadership",
    "mentoring": "Mentoring",
    "mentorship": "Mentoring",
    "problem solving": "Problem Solving",
    "collaboration": "Collaboration",
    "project management": "Project Management",

    # Integration
    "api integration": "API Integration",
    "etl": "ETL",
    "webhook": "Webhooks",
    "webhooks": "Webhooks",
    "ipaas": "iPaaS",
    "salesforce": "Salesforce",
    "sap": "SAP",
    "workday": "Workday",
}


# ── Parent category map ───────────────────────────────────────────────────────
# Skill → top-level category for grouping and partial credit

PARENT_MAP: dict[str, str] = {
    "Python": "programming",
    "JavaScript": "programming",
    "TypeScript": "programming",
    "Java": "programming",
    "Go": "programming",
    "Rust": "programming",
    "C": "programming",
    "C++": "programming",
    "C#": "programming",
    "Ruby": "programming",
    "PHP": "programming",
    "Swift": "programming",
    "Kotlin": "programming",
    "Scala": "programming",
    "R": "programming",
    "Bash": "programming",
    "Shell Scripting": "programming",
    "PowerShell": "programming",

    "React": "frontend",
    "Next.js": "frontend",
    "Vue.js": "frontend",
    "Angular": "frontend",
    "Svelte": "frontend",
    "HTML": "frontend",
    "CSS": "frontend",
    "Tailwind CSS": "frontend",
    "SASS": "frontend",
    "Redux": "frontend",

    "FastAPI": "backend",
    "Django": "backend",
    "Flask": "backend",
    "Express.js": "backend",
    "Spring Boot": "backend",
    "Rails": "backend",
    "Node.js": "backend",
    "NestJS": "backend",

    "GraphQL": "api",
    "REST API": "api",
    "gRPC": "api",
    "API Design": "api",
    "API Integration": "api",
    "WebSockets": "api",
    "Webhooks": "api",

    "PostgreSQL": "database",
    "MySQL": "database",
    "SQLite": "database",
    "MongoDB": "database",
    "Redis": "database",
    "Elasticsearch": "database",
    "Cassandra": "database",
    "DynamoDB": "database",
    "Oracle": "database",
    "SQL": "database",
    "NoSQL": "database",

    "AWS": "cloud",
    "Google Cloud Platform": "cloud",
    "Azure": "cloud",
    "Docker": "devops",
    "Kubernetes": "devops",
    "Terraform": "devops",
    "Ansible": "devops",
    "CI/CD": "devops",
    "Jenkins": "devops",
    "GitHub Actions": "devops",
    "GitLab CI": "devops",
    "Helm": "devops",
    "Linux": "devops",
    "Unix": "devops",

    "Machine Learning": "ml",
    "Deep Learning": "ml",
    "Natural Language Processing": "ml",
    "Computer Vision": "ml",
    "TensorFlow": "ml",
    "PyTorch": "ml",
    "Scikit-learn": "ml",
    "LLM": "ml",
    "Prompt Engineering": "ml",
    "RAG": "ml",
    "OpenAI API": "ml",
    "Langchain": "ml",
    "LlamaIndex": "ml",
    "Vector Databases": "ml",
    "Embeddings": "ml",
    "Agentic Frameworks": "ml",
    "LLM Observability": "ml",
    "AI Product Development": "ml",
    "Fine-Tuning": "ml",
    "Model Evaluation": "ml",

    "Pandas": "data",
    "NumPy": "data",
    "Spark": "data",
    "Hadoop": "data",
    "Airflow": "data",
    "Data Engineering": "data",
    "Data Analysis": "data",
    "ETL": "data",
    "Data Pipelines": "data",
    "Statistics": "data",
    "A/B Testing": "data",
    "Data Quality": "data",
    "Data Visualization": "data",
    "Product Analytics": "data",

    "Microservices": "architecture",
    "Event-Driven Architecture": "architecture",
    "Domain-Driven Design": "architecture",
    "System Design": "architecture",
    "Distributed Systems": "architecture",

    "Kafka": "messaging",
    "RabbitMQ": "messaging",
    "iPaaS": "integration",
    "Salesforce": "integration",
    "SAP": "integration",
    "Workday": "integration",

    "Agile": "process",
    "Scrum": "process",
    "Kanban": "process",
    "Test-Driven Development": "process",
    "Unit Testing": "process",
    "Integration Testing": "process",
    "Metrics-Driven Development": "process",
    "Requirements Analysis": "process",
    "Process Design": "process",

    "Git": "tools",
    "GitHub": "tools",
    "GitLab": "tools",
    "Jira": "tools",
    "Confluence": "tools",

    "OAuth": "security",
    "JWT": "security",
    "Security Best Practices": "security",
    "OWASP": "security",

    "Communication": "soft",
    "Leadership": "soft",
    "Mentoring": "soft",
    "Problem Solving": "soft",
    "Collaboration": "soft",
    "Project Management": "soft",
    "Stakeholder Management": "soft",
    "Presentation Skills": "soft",
    "Technical Writing": "soft",
    "Documentation": "soft",
    "Cross-Functional Collaboration": "soft",

    "MATLAB": "programming",
}


# ── Implication map (tool/skill → what it implies) ────────────────────────────
#
# Three-layer model:
#   TOOL (e.g. LangChain)  →  CANONICAL SKILL (e.g. RAG, Agentic Frameworks)
#   CANONICAL SKILL        →  CAPABILITY (e.g. Prompt Engineering → LLM)
#
# Rule: if a candidate HAS skill A, they get PARTIAL credit for skill B.
# This is how LangChain → Agentic Frameworks becomes a partial match.
#
IMPLICATION_MAP: dict[str, list[str]] = {
    # --- Language / framework implications ---
    "TypeScript": ["JavaScript"],
    "Next.js": ["React"],
    "NestJS": ["Node.js"],
    "Django": ["Python"],
    "Flask": ["Python"],
    "FastAPI": ["Python"],
    "Spring Boot": ["Java"],
    "Rails": ["Ruby"],

    # --- Data engineering ---
    "Spark": ["Data Engineering", "Data Pipelines"],
    "Airflow": ["Data Engineering", "Data Pipelines"],

    # --- ML framework chains ---
    "PyTorch": ["Machine Learning", "Deep Learning", "Model Evaluation"],
    "TensorFlow": ["Machine Learning", "Deep Learning", "Model Evaluation"],
    "Scikit-learn": ["Machine Learning", "Model Evaluation"],

    # --- Canonical ML → capability implications ---
    "Machine Learning": ["Model Evaluation", "Statistics"],
    "Deep Learning": ["Machine Learning", "Model Evaluation"],
    "Natural Language Processing": ["Machine Learning", "LLM"],

    # --- LLM / AI tool → capability chains ---
    # TOOL → CANONICAL CAPABILITY
    "LLM": [
        "Prompt Engineering",
        "Model Evaluation",     # knowing LLMs implies you need to evaluate them
        "AI Product Development",
    ],
    "Langchain": [
        "LLM",
        "RAG",
        "Prompt Engineering",
        "Agentic Frameworks",   # ← KEY: LangChain implies Agentic capability
        "Vector Databases",
        "Model Evaluation",
    ],
    "LlamaIndex": [
        "RAG",
        "LLM",
        "Vector Databases",
        "Embeddings",
        "Agentic Frameworks",   # ← LlamaIndex also implies Agentic capability
        "Model Evaluation",
    ],
    "RAG": [
        "LLM",
        "Embeddings",
        "Vector Databases",
        "Prompt Engineering",
        "Model Evaluation",
    ],
    "OpenAI API": [
        "LLM",
        "Prompt Engineering",
        "AI Product Development",
        "Model Evaluation",
    ],
    "Agentic Frameworks": [
        "LLM",
        "Prompt Engineering",
        "AI Product Development",
        "Model Evaluation",
    ],
    "Fine-Tuning": [
        "Machine Learning",
        "LLM",
        "AI Product Development",
        "Model Evaluation",
    ],
    "LLM Observability": [
        "LLM",
        "AI Product Development",
        "Model Evaluation",
    ],
    "Model Evaluation": [
        "Machine Learning",
        "Statistics",           # evaluation requires statistical reasoning
        "Data Analysis",
    ],
    "Vector Databases": [
        "Embeddings",
    ],

    # --- Data chains ---
    "Data Analysis": ["Statistics", "Data Quality"],
    "Data Engineering": ["Data Pipelines", "Data Quality"],
    "Pandas": ["Data Analysis", "Statistics"],
    "NumPy": ["Statistics", "Data Analysis"],
    "Statistics": ["Data Analysis"],

    # --- DevOps / infra ---
    "Kubernetes": ["Docker", "Microservices"],
    "GitHub Actions": ["CI/CD"],
    "GitLab CI": ["CI/CD"],
    "Jenkins": ["CI/CD"],

    # --- Architecture ---
    "Domain-Driven Design": ["System Design"],
    "Microservices": ["System Design", "Distributed Systems"],
    "Event-Driven Architecture": ["System Design", "Distributed Systems"],

    # --- Soft skill chains ---
    "Mentoring": ["Leadership", "Technical Writing", "Knowledge Sharing"],
    "Cross-Functional Collaboration": ["Collaboration", "Stakeholder Management"],
    "Stakeholder Management": ["Communication", "Collaboration"],
    "Technical Writing": ["Documentation", "Communication"],
}


def _normalize(name: str) -> str:
    """Lower-case, strip punctuation/extra spaces for alias lookup."""
    return re.sub(r"\s+", " ", name.strip().lower())


@lru_cache(maxsize=2048)
def canonicalize(skill_name: str) -> str:
    """Return the canonical name for a skill.

    Resolution order:
      1. Exact match in CANONICAL_SKILLS (case-insensitive)
      2. Exact alias map lookup (normalized input == alias key)
      3. Canonical name appears as whole-word in the input
         (e.g. "AWS Lambda" → "AWS", but NOT "engineering" → "R" or "Angular")
      4. Fallback: title-cased version of input (best-effort)

    NOTE: Intentionally NO substring alias matching ("alias in input" or
    "input in alias") — that caused catastrophic false positives such as
    "ng" (→Angular) matching inside "engineering", or "r" (→R) matching
    inside any word.  Aliases must be matched exactly.
    """
    if not skill_name or not skill_name.strip():
        return skill_name

    normalized = _normalize(skill_name)

    # 1. Case-insensitive exact match against canonical set
    for canonical in CANONICAL_SKILLS:
        if _normalize(canonical) == normalized:
            return canonical

    # 2. Exact alias lookup only — NO substring matching
    if normalized in ALIAS_MAP:
        return ALIAS_MAP[normalized]

    # 3. Canonical name as a whole-word match inside the input
    #    e.g. "AWS Lambda" contains whole word "AWS"
    #    but "engineering" does NOT contain whole word "R"
    for canonical in sorted(CANONICAL_SKILLS, key=len, reverse=True):
        pattern = r'(?<![\w])' + re.escape(_normalize(canonical)) + r'(?![\w])'
        if re.search(pattern, normalized):
            return canonical

    # 4. Fallback: title-cased version of input (preserves the skill as-is)
    return skill_name.strip().title()


def get_parent_category(canonical_skill: str) -> Optional[str]:
    """Return the parent category for a canonical skill name."""
    return PARENT_MAP.get(canonical_skill)


def get_implied_skills(canonical_skill: str) -> list[str]:
    """Return skills partially implied by possessing this skill."""
    return IMPLICATION_MAP.get(canonical_skill, [])


def resolve_skills(raw_skills: list[str | dict]) -> list[str]:
    """Canonicalize a list of skills (strings or {name:...} dicts).

    Returns deduplicated canonical names in original order.
    """
    seen: set[str] = set()
    result: list[str] = []
    for item in raw_skills:
        if isinstance(item, dict):
            name = item.get("name", "")
        else:
            name = str(item)
        canonical = canonicalize(name)
        if canonical not in seen:
            seen.add(canonical)
            result.append(canonical)
    return result


def skills_share_parent(skill_a: str, skill_b: str) -> bool:
    """True if two canonical skills belong to the same top-level category."""
    pa = PARENT_MAP.get(skill_a)
    pb = PARENT_MAP.get(skill_b)
    return pa is not None and pa == pb
