# ⚡ HireAI — Intelligent Hiring Copilot


Explainable AI Decision Systems for Resume Evaluation

Modern AI can generate answers.
But real-world decision systems require reasoning, transparency, and guardrails.

This project explores how to build an Explainable AI decision engine that evaluates candidates against job requirements and produces traceable hiring recommendations.

Instead of relying purely on LLM outputs, the system combines:

structured signal extraction
deterministic evaluation logic
explainable scoring
LLM-assisted semantic parsing
guardrailed reasoning pipelines

The result is an AI system that can justify its decisions rather than simply generate them.


The engine also identifies:

- critical skill gaps  
- strength signals  
- confidence drivers  


## Explainable Evaluation Output

Each evaluation produces a structured reasoning report containing:

### Recommendation
Decision label indicating candidate suitability.

### Confidence Level
Signal reliability based on evidence coverage.

### Skill Coverage Analysis
Evidence-backed matches between candidate capabilities and job requirements.

### Strengths and Risks
Positive and negative signals influencing the evaluation.

### Decision Trace
Step-by-step reasoning chain explaining how the outcome was derived.

### Composite Score Breakdown
Weighted contribution from each evaluation dimension.
    The system surfaces both the **numerical outcome and the reasoning behind it**.


## Safety and Guardrails

To ensure reliable AI-assisted decisions, the system enforces several guardrails.

### Evidence Grounding
Skills must reference supporting resume text.

### Deterministic Scoring
Identical inputs produce identical outputs.

### Critical Gap Detection
Missing core requirements cannot be masked by secondary strengths.

### Protected Attribute Exclusion
Sensitive attributes are ignored during evaluation.

### LLM Output Validation
All AI outputs are schema-validated before entering the pipeline.

### Decision Traceability
Every evaluation stores the signals and reasoning steps used to generate the recommendation.



## Architecture

<img width="1536" height="1024" alt="resume-evaluation-arch" src="https://github.com/user-attachments/assets/c48b5c5b-fe09-4d64-8d90-8822b5e4b431" />



## Quick Start


```
HireAI/
├── backend/              # FastAPI Python backend
│   ├── app/
│   │   ├── api/routes/   # REST API endpoints
│   │   ├── core/         # Config, database
│   │   ├── models/       # SQLAlchemy ORM models
│   │   ├── schemas/      # Pydantic validation schemas
│   │   ├── services/     # LLM abstraction, evaluation pipeline
│   │   └── utils/        # File parsing utilities
│   └── requirements.txt
├── frontend/             # Next.js React frontend
│   ├── app/              # Next.js app router pages
│   ├── components/       # React components
│   └── lib/              # API client
└── README.md
```

<img width="619" height="449" alt="Screenshot 2026-03-31 at 4 00 35 PM" src="https://github.com/user-attachments/assets/f555fd39-a38b-4ec8-aed1-26e3cce60ed5" />



### Prerequisites

- Python 3.11+
- Node.js 18+
- [Ollama](https://ollama.ai) running locally with `llama3.2`

### 1. Start Ollama

```bash
ollama pull llama3.2
ollama serve
```

### 2. Start Backend

```bash
cd backend
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

### 3. Start Frontend

```bash
cd frontend
npm install
npm run dev
```

Open [http://localhost:3000](http://localhost:3000)

## Switching LLM Providers

Edit `backend/.env`:

```bash
# Use OpenAI
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-your-key
OPENAI_MODEL=gpt-4o

# Use Anthropic
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-your-key
ANTHROPIC_MODEL=claude-3-5-sonnet-20241022
```

## API Documentation

Once the backend is running, visit:
- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc

## Features

- ✅ Job Requisition management with AI-powered JD parsing
- ✅ Resume upload & text extraction (PDF, DOCX, TXT)
- ✅ AI candidate evaluation with structured output
- ✅ Skill matching with evidence
- ✅ Decision trace (explainability)
- ✅ Confidence scoring & calibration
- ✅ Human override with audit trail
- ✅ Dashboard analytics
- ✅ Swappable LLM backend (Ollama → OpenAI → Anthropic)

## Backlog

---

## HireAI — Production Readiness Feature List

---

### P0 — Blockers (must fix before any real traffic)

#### Infrastructure

Here's the sequence:

- Now: Vercel + Render + Groq + Sentry 
- First 100 users: Add Cloudflare, rate limiting, Supabase
- First 1000 users: Dockerize, move to Railway/Fly.io, add Redis cache
- First 10,000 users: Add job queue, API gateway, Datadog

  <img width="682" height="513" alt="Screenshot 2026-03-31 at 4 00 24 PM" src="https://github.com/user-attachments/assets/a10d6655-cd6c-4f45-be93-b458a642d7b7" />


| # | Feature | Why |
|---|---|---|
| 1 | **Replace SQLite → PostgreSQL** | SQLite is file-locked, single-writer, not network-accessible. Every concurrent evaluation contends on one lock. |
| 2 | **Authentication & authorisation** | Zero auth today — any client can read/write/delete all candidates. Need JWT or session auth + role-based access (recruiter / hiring manager / admin). |
| 3 | **Multi-tenancy** | All data is global. Need `org_id` on every model so Company A can't see Company B's candidates. |
| 4 | **Async evaluation queue** | Evaluations run inline in the HTTP request. One 40s LLM call blocks the server. Need Celery + Redis (or ARQ) — SSE stream from queue worker, not from the request thread. |
| 5 | **File storage → object store** | Resumes saved to local disk (`/uploads`). Dies on restart, breaks with multiple server instances. Need S3 / GCS with signed URLs. |
| 6 | **Secrets management** | API keys in `.env` file. Need Vault, AWS Secrets Manager, or at minimum environment injection via CI — never committed to repo. |

---

### P1 — High (needed for stable operation)

#### Reliability
| # | Feature | Why |
|---|---|---|
| 7 | **LLM circuit breaker** | If Ollama / OpenAI is down, evaluations silently fall back or hang. Need a circuit breaker with dead-letter queue and retry with backoff. |
| 8 | **Evaluation idempotency** | Re-evaluating the same candidate twice creates two rows. Need `(candidate_id, jd_id, engine_version)` unique key + upsert semantics. |
| 9 | **DB migrations (Alembic)** | Schema changes today require manual `DROP TABLE`. Need versioned migrations to safely evolve the schema in prod without data loss. |
| 10 | **Rate limiting** | No rate limits on the API. A single client can trigger hundreds of LLM evaluations simultaneously, exhausting Ollama. |
| 11 | **Request timeout enforcement** | LLM calls can hang indefinitely. Need per-stage timeouts (not just `httpx` client timeout) with graceful SSE error events. |

#### Observability
| # | Feature | Why |
|---|---|---|
| 12 | **Structured metrics** | Log files exist but no metrics. Need Prometheus counters/histograms for: evaluations/s, LLM latency p50/p95, pipeline stage timing, fallback rate. |
| 13 | **Distributed tracing** | `trace_id` exists but isn't propagated to OpenTelemetry/Jaeger. Can't correlate a slow evaluation across frontend SSE → API → LLM → DB. |
| 14 | **Error alerting** | No alerting. Silent fallbacks (keyword heuristic, partial evaluations) happen invisibly. Need alerting on fallback rate spike, LLM failure rate, evaluation error rate. |
| 15 | **Health check endpoint** | `/health` exists but only returns `{"status": "ok"}`. Should check: DB connectivity, LLM reachability, queue depth. |

---

### P2 — Medium (needed for team use)

#### Product completeness
| # | Feature | Why |
|---|---|---|
| 16 | **Job Requisition management UI** | JDs are created via API only. Recruiters need a UI to create, edit, clone, and archive requisitions. |
| 17 | **Bulk candidate upload** | Upload a ZIP of resumes or a CSV of LinkedIn profiles. Today it's one-by-one. |
| 18 | **Evaluation history & versioning** | When a candidate is re-evaluated, old scores are overwritten. Need a full history with `engine_version` diff so you can see how scores changed after a JD update. |
| 19 | **Recruiter override audit trail** | Overrides are stored but not surfaced in a way that closes the feedback loop with the signal engine. Override patterns should feed calibration. |
| 20 | **Candidate comparison view** | Side-by-side Score Breakdown across 2–4 candidates shortlisted for the same role. |
| 21 | **Export to ATS** | One-click export of evaluation + recommendation to Greenhouse / Lever / Workday via webhook or CSV. |

#### Pipeline quality
| # | Feature | Why |
|---|---|---|
| 22 | **Ontology expansion UI** | The skill ontology is hardcoded in `ontology.py`. Skills not in the ontology get no canonical mapping and no implication chains. Need an admin UI to add/edit skills, synonyms, and implications. |
| 23 | **Evaluation calibration dashboard** | Track: recommendation accuracy over time, override rate per recruiter, score drift across engine versions, false positive/negative rate against hire outcomes. |
| 24 | **JD quality scoring** | Bad JDs (vague, 50 required skills, no seniority signal) produce unreliable evaluations. Detect and warn before evaluation runs. |
| 25 | **Confidence threshold tuning** | `LOW_CONFIDENCE_THRESHOLD` is a hardcoded config value. Need per-role, per-team tuning with historical data backing the choice. |

---



