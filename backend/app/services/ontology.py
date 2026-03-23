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

    # Integration / Enterprise
    "API Integration", "ETL", "Data Pipelines", "Webhooks",
    "iPaaS", "Salesforce", "SAP", "Workday",

    # LLM / AI (modern)
    "LLM", "Prompt Engineering", "RAG", "OpenAI API", "Langchain",
    "Vector Databases", "Embeddings",
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
    "dl": "Deep Learning",
    "deep learning": "Deep Learning",
    "nlp": "Natural Language Processing",
    "natural language processing": "Natural Language Processing",
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
    "large language model": "LLM",
    "prompt engineering": "Prompt Engineering",
    "rag": "RAG",
    "retrieval augmented generation": "RAG",
    "langchain": "Langchain",
    "openai": "OpenAI API",
    "chatgpt": "OpenAI API",
    "vector db": "Vector Databases",
    "vector database": "Vector Databases",
    "embeddings": "Embeddings",

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
    "Vector Databases": "ml",
    "Embeddings": "ml",

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
    "MATLAB": "programming",
}


# ── Implication map (parent skill implies partial credit for children) ─────────
# If candidate has skill A, they get partial credit for skill B
IMPLICATION_MAP: dict[str, list[str]] = {
    "TypeScript": ["JavaScript"],
    "Next.js": ["React"],
    "NestJS": ["Node.js"],
    "PyTorch": ["Machine Learning", "Deep Learning"],
    "TensorFlow": ["Machine Learning", "Deep Learning"],
    "Scikit-learn": ["Machine Learning"],
    "Django": ["Python"],
    "Flask": ["Python"],
    "FastAPI": ["Python"],
    "Spring Boot": ["Java"],
    "Rails": ["Ruby"],
    "Spark": ["Data Engineering", "Data Pipelines"],
    "Airflow": ["Data Engineering", "Data Pipelines"],
    "RAG": ["LLM", "Embeddings", "Vector Databases"],
    "Langchain": ["LLM"],
    "Kubernetes": ["Docker"],
    "GitHub Actions": ["CI/CD"],
    "GitLab CI": ["CI/CD"],
    "Jenkins": ["CI/CD"],
    "Domain-Driven Design": ["System Design"],
    "Microservices": ["System Design"],
}


def _normalize(name: str) -> str:
    """Lower-case, strip punctuation/extra spaces for alias lookup."""
    return re.sub(r"\s+", " ", name.strip().lower())


@lru_cache(maxsize=2048)
def canonicalize(skill_name: str) -> str:
    """Return the canonical name for a skill.

    Resolution order:
      1. Exact match in CANONICAL_SKILLS (case-insensitive)
      2. Alias map lookup
      3. Fuzzy: longest canonical name that's a substring of the input
      4. Fallback: title-cased version of input (best-effort)
    """
    if not skill_name or not skill_name.strip():
        return skill_name

    normalized = _normalize(skill_name)

    # 1. Case-insensitive exact match
    for canonical in CANONICAL_SKILLS:
        if _normalize(canonical) == normalized:
            return canonical

    # 2. Alias map
    if normalized in ALIAS_MAP:
        return ALIAS_MAP[normalized]

    # 3. Partial alias match (input contains the alias)
    for alias, canonical in ALIAS_MAP.items():
        if alias in normalized or normalized in alias:
            return canonical

    # 4. Canonical as substring of input (e.g. "AWS Lambda" → "AWS")
    for canonical in sorted(CANONICAL_SKILLS, key=len, reverse=True):
        if _normalize(canonical) in normalized:
            return canonical

    # 5. Fallback: best-effort title case
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
