"""FastAPI application entry point with comprehensive request logging."""

import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import settings
from app.core.database import init_db
from app.api.routes import requisitions, candidates, dashboard

# ── Logging Configuration ────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)-40s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# Quiet down noisy libraries in DEBUG mode
logging.getLogger("aiosqlite").setLevel(logging.WARNING)
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("watchfiles").setLevel(logging.WARNING)

logger = logging.getLogger("hireai.app")


# ── Request Logging Middleware ───────────────────────────────────────────────

class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Logs every request/response with timing, correlation IDs, and status."""

    async def dispatch(self, request: Request, call_next):
        # Generate a unique correlation ID for tracing this request
        correlation_id = uuid.uuid4().hex[:8].upper()
        request.state.correlation_id = correlation_id

        method = request.method
        path = request.url.path
        query = str(request.url.query) if request.url.query else ""
        client = request.client.host if request.client else "unknown"

        # Skip noisy health checks from frontend polling
        is_health = "/health" in path

        if not is_health:
            logger.info(
                f"[{correlation_id}] ▶ {method} {path}"
                f"{'?' + query if query else ''}"
                f" | client={client}"
            )

        start_time = time.time()

        try:
            response = await call_next(request)
            elapsed_ms = int((time.time() - start_time) * 1000)

            if not is_health:
                status = response.status_code
                level = logging.INFO if status < 400 else logging.WARNING if status < 500 else logging.ERROR
                logger.log(
                    level,
                    f"[{correlation_id}] ◀ {method} {path} → {status}"
                    f" | {elapsed_ms}ms"
                )

            # Inject correlation ID into response headers for client-side tracing
            response.headers["X-Correlation-ID"] = correlation_id
            response.headers["X-Response-Time-Ms"] = str(elapsed_ms)
            return response

        except Exception as exc:
            elapsed_ms = int((time.time() - start_time) * 1000)
            logger.error(
                f"[{correlation_id}] ✖ {method} {path} → EXCEPTION"
                f" | {elapsed_ms}ms | {type(exc).__name__}: {exc}"
            )
            raise


# ── Lifespan ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle: startup and shutdown."""
    logger.info("=" * 70)
    logger.info(f"  Starting {settings.APP_NAME} v{settings.APP_VERSION}")
    logger.info(f"  LLM Provider: {settings.LLM_PROVIDER} | Model: {settings.LLM_MODEL}")
    logger.info(f"  Database: {settings.DATABASE_URL}")
    logger.info(f"  Debug: {settings.DEBUG}")
    logger.info("=" * 70)

    await init_db()
    logger.info("Database tables initialized successfully")

    yield

    logger.info("Application shutting down gracefully")


# ── App ──────────────────────────────────────────────────────────────────────

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="AI-assisted candidate evaluation system",
    lifespan=lifespan,
)

# Middleware (order matters: first added = outermost)
app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routes
app.include_router(requisitions.router, prefix="/api/v1")
app.include_router(candidates.router, prefix="/api/v1")
app.include_router(dashboard.router, prefix="/api/v1")


@app.get("/")
async def root():
    logger.debug("Root endpoint hit")
    return {
        "name": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "docs": "/docs",
    }
