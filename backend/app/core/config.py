"""Application configuration using Pydantic Settings."""

from pathlib import Path
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Global application settings loaded from environment variables."""

    # App
    APP_NAME: str = "Intelligent Hiring Copilot"
    APP_VERSION: str = "2.0.0"
    DEBUG: bool = True

    # Server
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    # Database
    DATABASE_URL: str = "sqlite+aiosqlite:///./data/hireai.db"

    # File uploads
    UPLOAD_DIR: str = str(Path(__file__).resolve().parent.parent.parent / "uploads")
    MAX_UPLOAD_SIZE_MB: int = 10

    # LLM Configuration — used for parsing ONLY (D2/D3 stages)
    LLM_PROVIDER: str = "ollama"  # ollama | openai | anthropic
    LLM_BASE_URL: str = "http://localhost:11434"
    LLM_MODEL: str = "llama3.2"
    LLM_TEMPERATURE: float = 0.1  # Low for deterministic parsing outputs
    LLM_MAX_TOKENS: int = 8192
    LLM_TIMEOUT: int = 120       # seconds — passed to asyncio.wait_for

    # OpenAI
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4o"

    # Anthropic
    ANTHROPIC_API_KEY: str = ""
    ANTHROPIC_MODEL: str = "claude-3-5-sonnet-20241022"

    # ── Pipeline: retry & resilience ─────────────────────────────────────────
    # LLM retries (per stage, exponential backoff)
    LLM_MAX_RETRIES: int = 2
    LLM_RETRY_DELAY_S: float = 1.5

    # Circuit breaker: consecutive failures before tripping
    LLM_CIRCUIT_BREAKER_THRESHOLD: int = 5
    LLM_CIRCUIT_BREAKER_RESET_S: int = 60

    # ── Pipeline: decision thresholds ────────────────────────────────────────
    # These map to DecisionAgent tiering — change here, nowhere else.
    HIGH_CONFIDENCE_THRESHOLD: float = 0.75
    LOW_CONFIDENCE_THRESHOLD: float = 0.40
    MANUAL_REVIEW_THRESHOLD: float = 0.50

    # Score-based decision tiers (composite 0–100)
    SCORE_STRONG_HIRE: float = 78.0
    SCORE_HIRE: float = 62.0
    SCORE_CONSIDER: float = 42.0

    # ── Pipeline: caching ────────────────────────────────────────────────────
    # In-process parse cache (avoids redundant LLM calls)
    ENABLE_PARSE_CACHE: bool = True
    PARSE_CACHE_MAX_ENTRIES: int = 500  # LRU eviction above this

    # ── Pipeline: feature flags ───────────────────────────────────────────────
    # When True: decision is fully deterministic (no LLM for eval stage)
    SIGNAL_DRIVEN_MODE: bool = True

    # When True: return partial fallback instead of hard error on pipeline failure
    ENABLE_PARTIAL_FALLBACK: bool = True

    # ── CORS ──────────────────────────────────────────────────────────────────
    CORS_ORIGINS: list[str] = ["http://localhost:3000", "http://localhost:3001"]

    class Config:
        env_file = ".env"
        case_sensitive = True


settings = Settings()
