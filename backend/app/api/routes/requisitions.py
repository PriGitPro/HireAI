"""Job Requisition API routes with comprehensive logging."""

import json
import logging
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.models.models import JobRequisition, Candidate
from app.schemas.schemas import (
    JobRequisitionCreate,
    JobRequisitionResponse,
    JobRequisitionList,
)
from app.services.evaluation_service import EvaluationService

logger = logging.getLogger("hireai.api.requisitions")
router = APIRouter(prefix="/requisitions", tags=["Job Requisitions"])


@router.post("", response_model=JobRequisitionResponse, status_code=201)
async def create_requisition(
    data: JobRequisitionCreate,
    db: AsyncSession = Depends(get_db),
):
    """Create a new job requisition and auto-parse the description."""
    start = time.time()
    logger.info(
        f"REQUISITION.create | START"
        f" | title=\"{data.title}\""
        f" | department={data.department}"
        f" | location={data.location}"
        f" | jd_length={len(data.description_raw)} chars"
    )

    req = JobRequisition(
        title=data.title,
        department=data.department,
        location=data.location,
        employment_type=data.employment_type,
        description_raw=data.description_raw,
    )
    db.add(req)
    await db.flush()
    logger.info(f"REQUISITION.create | Requisition saved | id={req.id}")

    # Auto-parse JD
    try:
        parse_start = time.time()
        logger.info(f"REQUISITION.create | Auto-parsing JD via LLM...")
        svc = EvaluationService()
        parsed_jd = await svc.parse_job_description(data.description_raw)
        req.description_structured = json.loads(parsed_jd.model_dump_json())
        req.required_skills = [
            {"name": s.canonical_name, "importance": s.importance.value, "category": s.category.value}
            for s in parsed_jd.required_skills
        ]
        req.experience_requirements = parsed_jd.experience_requirements.model_dump()
        req.education_requirements = parsed_jd.education_requirements.model_dump()
        db.add(req)

        skills_count = len(req.required_skills or [])
        parse_elapsed = int((time.time() - parse_start) * 1000)
        logger.info(
            f"REQUISITION.create | JD parsed successfully"
            f" | skills_extracted={skills_count}"
            f" | parse_time={parse_elapsed}ms"
        )
    except Exception as e:
        logger.warning(
            f"REQUISITION.create | JD auto-parse FAILED (non-blocking)"
            f" | error={type(e).__name__}: {e}"
        )

    await db.flush()
    await db.refresh(req)

    elapsed = int((time.time() - start) * 1000)
    logger.info(
        f"REQUISITION.create | COMPLETE"
        f" | id={req.id}"
        f" | title=\"{req.title}\""
        f" | total_time={elapsed}ms"
    )

    return _to_response(req, 0)


@router.get("", response_model=JobRequisitionList)
async def list_requisitions(
    status: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """List all job requisitions with candidate counts."""
    logger.debug(f"REQUISITION.list | status_filter={status} | skip={skip} | limit={limit}")

    query = select(JobRequisition)
    if status:
        query = query.where(JobRequisition.status == status)
    query = query.order_by(JobRequisition.created_at.desc()).offset(skip).limit(limit)

    result = await db.execute(query)
    requisitions = result.scalars().all()

    items = []
    for req in requisitions:
        count_q = await db.execute(
            select(func.count()).where(Candidate.requisition_id == req.id)
        )
        count = count_q.scalar() or 0
        items.append(_to_response(req, count))

    total_q = select(func.count()).select_from(JobRequisition)
    if status:
        total_q = total_q.where(JobRequisition.status == status)
    total_result = await db.execute(total_q)
    total = total_result.scalar() or 0

    logger.debug(f"REQUISITION.list | returned={len(items)} | total={total}")
    return JobRequisitionList(items=items, total=total)


@router.get("/{req_id}", response_model=JobRequisitionResponse)
async def get_requisition(
    req_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Get a single requisition by ID."""
    logger.debug(f"REQUISITION.get | id={req_id}")

    req = await db.get(JobRequisition, req_id)
    if not req:
        logger.warning(f"REQUISITION.get | Not found: {req_id}")
        raise HTTPException(status_code=404, detail="Requisition not found")

    count_q = await db.execute(
        select(func.count()).where(Candidate.requisition_id == req.id)
    )
    count = count_q.scalar() or 0

    logger.debug(f"REQUISITION.get | Found \"{req.title}\" | candidates={count}")
    return _to_response(req, count)


@router.delete("/{req_id}", status_code=204)
async def delete_requisition(
    req_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Delete a requisition and all associated data."""
    req = await db.get(JobRequisition, req_id)
    if not req:
        logger.warning(f"REQUISITION.delete | Not found: {req_id}")
        raise HTTPException(status_code=404, detail="Requisition not found")

    logger.info(f"REQUISITION.delete | Deleting \"{req.title}\" ({req_id}) and all associated data")
    await db.delete(req)


def _to_response(req: JobRequisition, candidate_count: int) -> JobRequisitionResponse:
    return JobRequisitionResponse(
        id=req.id,
        title=req.title,
        department=req.department,
        location=req.location,
        employment_type=req.employment_type,
        description_raw=req.description_raw,
        description_structured=req.description_structured,
        required_skills=req.required_skills,
        experience_requirements=req.experience_requirements,
        education_requirements=req.education_requirements,
        status=req.status,
        created_at=req.created_at,
        updated_at=req.updated_at,
        candidate_count=candidate_count,
    )
