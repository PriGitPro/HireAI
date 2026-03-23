# ⚡ HireAI — Intelligent Hiring Copilot

AI-assisted candidate evaluation system that produces structured hiring recommendations with evidence-backed reasoning.

## Architecture

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

## Quick Start

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
