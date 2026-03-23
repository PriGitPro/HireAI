"""Dashboard and analytics routes with logging."""

import logging
import time

from fastapi import APIRouter, Depends
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.models.models import JobRequisition, Candidate, Evaluation
from app.schemas.schemas import DashboardStats
from app.services.llm_provider import get_llm_provider

logger = logging.getLogger("hireai.api.dashboard")
router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


@router.get("/stats", response_model=DashboardStats)
async def get_dashboard_stats(db: AsyncSession = Depends(get_db)):
    """Get aggregate dashboard statistics."""
    start = time.time()
    logger.debug("DASHBOARD.stats | Aggregating statistics...")

    # Requisitions
    total_req = (await db.execute(select(func.count()).select_from(JobRequisition))).scalar() or 0
    active_req = (
        await db.execute(
            select(func.count())
            .select_from(JobRequisition)
            .where(JobRequisition.status == "active")
        )
    ).scalar() or 0

    # Candidates
    total_cand = (await db.execute(select(func.count()).select_from(Candidate))).scalar() or 0
    evaluated = (
        await db.execute(
            select(func.count())
            .select_from(Candidate)
            .where(Candidate.status == "evaluated")
        )
    ).scalar() or 0
    pending = (
        await db.execute(
            select(func.count())
            .select_from(Candidate)
            .where(Candidate.status == "pending")
        )
    ).scalar() or 0
    flagged = (
        await db.execute(
            select(func.count())
            .select_from(Candidate)
            .where(Candidate.status == "flagged")
        )
    ).scalar() or 0

    # Average confidence
    avg_conf_result = await db.execute(select(func.avg(Evaluation.confidence)))
    avg_confidence = avg_conf_result.scalar()

    # Recommendation distribution
    rec_dist = {}
    for label in ["strong_hire", "hire", "maybe", "no_hire"]:
        count = (
            await db.execute(
                select(func.count())
                .select_from(Evaluation)
                .where(Evaluation.recommendation == label)
            )
        ).scalar() or 0
        rec_dist[label] = count

    elapsed = int((time.time() - start) * 1000)
    logger.debug(
        f"DASHBOARD.stats | COMPLETE | {elapsed}ms"
        f" | reqs={total_req}(active={active_req})"
        f" | candidates={total_cand}(eval={evaluated},pending={pending},flagged={flagged})"
        f" | avg_conf={avg_confidence:.3f if avg_confidence else 'N/A'}"
        f" | dist={rec_dist}"
    )

    return DashboardStats(
        total_requisitions=total_req,
        active_requisitions=active_req,
        total_candidates=total_cand,
        evaluated_candidates=evaluated,
        pending_candidates=pending,
        flagged_candidates=flagged,
        avg_confidence=round(avg_confidence, 3) if avg_confidence else None,
        recommendation_distribution=rec_dist,
    )


@router.get("/health")
async def health_check():
    """System health check including LLM status."""
    start = time.time()
    llm_healthy = False
    try:
        provider = get_llm_provider()
        llm_healthy = await provider.health_check()
    except Exception as e:
        logger.warning(f"DASHBOARD.health | LLM health check failed: {type(e).__name__}: {e}")

    elapsed = int((time.time() - start) * 1000)
    logger.debug(
        f"DASHBOARD.health | llm_connected={llm_healthy}"
        f" | provider={settings.LLM_PROVIDER}"
        f" | {elapsed}ms"
    )

    return {
        "status": "healthy",
        "llm_connected": llm_healthy,
        "llm_provider": settings.LLM_PROVIDER,
    }
