"""Candidate management and evaluation API routes.

Includes SSE streaming endpoint for real-time evaluation progress.

Logging covers:
- Candidate creation with resume handling
- Resume upload (file) and paste (text) paths
- SSE streaming evaluation with per-stage events
- Evaluation requests and results
- Human override flow
- Audit trail queries
"""

import json
import logging
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.database import get_db, async_session_factory
from app.models.models import Candidate, Evaluation, AuditLog, JobRequisition
from app.schemas.schemas import (
    CandidateCreate,
    CandidateResponse,
    CandidateDetail,
    EvaluationResponse,
    EvaluateCandidateRequest,
    OverrideRequest,
    AuditLogResponse,
)
from app.services.evaluation_service import EvaluationService
from app.utils.file_parser import extract_text_from_file

logger = logging.getLogger("hireai.api.candidates")
router = APIRouter(prefix="/requisitions/{req_id}/candidates", tags=["Candidates"])


# ── SSE Streaming Evaluation ────────────────────────────────────────────────


@router.get("/{candidate_id}/evaluate/stream")
async def evaluate_candidate_stream(
    req_id: str,
    candidate_id: str,
    force: bool = Query(False),
):
    """Stream evaluation progress via Server-Sent Events (SSE).

    Opens an SSE connection and pushes events as each pipeline stage completes:
      - stage:   Progress update (loading, jd_parsing, resume_parsing, evaluating, saving)
      - cached:  Returning existing evaluation
      - result:  Final evaluation data
      - error:   Error during pipeline
      - done:    Stream complete

    Usage: const es = new EventSource('/api/v1/requisitions/{req_id}/candidates/{id}/evaluate/stream');
    """
    logger.info(
        f"SSE.evaluate | Connection opened"
        f" | candidate_id={candidate_id}"
        f" | force={force}"
    )

    async def event_generator():
        """Async generator that yields SSE-formatted text."""
        # SSE needs its own DB session since StreamingResponse
        # runs AFTER the request dependency lifecycle
        async with async_session_factory() as db:
            try:
                # Verify candidate belongs to requisition
                candidate = await db.get(Candidate, candidate_id)
                if not candidate or candidate.requisition_id != req_id:
                    yield _format_sse("error", {"message": "Candidate not found"})
                    yield _format_sse("done", {})
                    return

                if not candidate.resume_text:
                    yield _format_sse("error", {"message": "No resume available. Upload a resume first."})
                    yield _format_sse("done", {})
                    return

                svc = EvaluationService()

                async for event in svc.evaluate_candidate_streaming(
                    db=db,
                    candidate_id=candidate_id,
                    force_reevaluate=force,
                ):
                    yield _format_sse(event["event"], event["data"])

                await db.commit()
                logger.info(f"SSE.evaluate | Stream complete | candidate_id={candidate_id}")

            except Exception as e:
                logger.error(f"SSE.evaluate | Stream error: {type(e).__name__}: {e}", exc_info=True)
                await db.rollback()
                yield _format_sse("error", {"message": str(e)})
                yield _format_sse("done", {})

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        },
    )


def _format_sse(event: str, data: dict) -> str:
    """Format a dict as an SSE message string."""
    json_data = json.dumps(data, default=str)
    return f"event: {event}\ndata: {json_data}\n\n"


# ── Standard REST Endpoints ─────────────────────────────────────────────────


@router.post("", response_model=CandidateResponse, status_code=201)
async def create_candidate(
    req_id: str,
    name: str = Form(...),
    email: str = Form(None),
    phone: str = Form(None),
    resume: UploadFile = File(None),
    resume_text: str = Form(None),
    db: AsyncSession = Depends(get_db),
):
    """Create a new candidate with optional resume (file upload OR pasted text).

    Returns immediately. Frontend should open SSE stream to trigger evaluation.
    """
    start_time = time.time()

    req = await db.get(JobRequisition, req_id)
    if not req:
        logger.warning(f"CANDIDATE.create | Requisition {req_id} not found")
        raise HTTPException(status_code=404, detail="Requisition not found")

    logger.info(
        f"CANDIDATE.create | START"
        f" | requisition_id={req_id}"
        f" | name=\"{name}\""
        f" | has_file={'yes' if resume and resume.filename else 'no'}"
        f" | has_pasted_text={'yes' if resume_text else 'no'}"
    )

    candidate = Candidate(
        requisition_id=req_id,
        name=name,
        email=email,
        phone=phone,
    )

    # Handle resume — prioritize file upload over pasted text
    if resume and resume.filename:
        logger.info(f"CANDIDATE.create | Processing resume file upload: {resume.filename}")
        candidate = await _handle_resume_upload(candidate, resume)
    elif resume_text and resume_text.strip():
        logger.info(
            f"CANDIDATE.create | Processing pasted resume text"
            f" | text_length={len(resume_text)} chars"
        )
        candidate.resume_text = resume_text.strip()
        candidate.resume_filename = "pasted_resume.txt"

    db.add(candidate)
    await db.flush()
    await db.refresh(candidate)

    elapsed = int((time.time() - start_time) * 1000)
    logger.info(
        f"CANDIDATE.create | COMPLETE"
        f" | id={candidate.id}"
        f" | has_resume_text={'yes' if candidate.resume_text else 'no'}"
        f" | {elapsed}ms"
    )

    return _to_candidate_response(candidate, False)


@router.get("", response_model=list[CandidateResponse])
async def list_candidates(
    req_id: str,
    db: AsyncSession = Depends(get_db),
):
    """List all candidates for a requisition."""
    logger.debug(f"CANDIDATE.list | requisition_id={req_id}")

    result = await db.execute(
        select(Candidate)
        .where(Candidate.requisition_id == req_id)
        .order_by(Candidate.created_at.desc())
    )
    candidates = result.scalars().all()

    items = []
    for c in candidates:
        eval_q = await db.execute(
            select(Evaluation.id).where(Evaluation.candidate_id == c.id)
        )
        has_eval = eval_q.scalar_one_or_none() is not None
        items.append(_to_candidate_response(c, has_eval))

    logger.debug(f"CANDIDATE.list | requisition_id={req_id} | count={len(items)}")
    return items


@router.get("/{candidate_id}", response_model=CandidateDetail)
async def get_candidate(
    req_id: str,
    candidate_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Get full candidate details including evaluation."""
    logger.debug(f"CANDIDATE.get | candidate_id={candidate_id}")

    candidate = await db.get(Candidate, candidate_id)
    if not candidate or candidate.requisition_id != req_id:
        logger.warning(f"CANDIDATE.get | Candidate {candidate_id} not found in requisition {req_id}")
        raise HTTPException(status_code=404, detail="Candidate not found")

    eval_q = await db.execute(
        select(Evaluation).where(Evaluation.candidate_id == candidate_id)
    )
    evaluation = eval_q.scalar_one_or_none()

    return _to_candidate_detail(candidate, evaluation)


@router.post("/{candidate_id}/evaluate", response_model=EvaluationResponse)
async def evaluate_candidate(
    req_id: str,
    candidate_id: str,
    body: EvaluateCandidateRequest = EvaluateCandidateRequest(),
    db: AsyncSession = Depends(get_db),
):
    """Trigger AI evaluation (synchronous fallback — prefer SSE stream)."""
    logger.info(
        f"CANDIDATE.evaluate | Sync API trigger"
        f" | candidate_id={candidate_id}"
        f" | force={body.force_reevaluate}"
    )

    candidate = await db.get(Candidate, candidate_id)
    if not candidate or candidate.requisition_id != req_id:
        raise HTTPException(status_code=404, detail="Candidate not found")

    if not candidate.resume_text:
        raise HTTPException(status_code=400, detail="No resume uploaded for this candidate")

    try:
        svc = EvaluationService()
        evaluation = await svc.evaluate_candidate(
            db=db,
            candidate_id=candidate_id,
            force_reevaluate=body.force_reevaluate,
        )
        return _to_evaluation_response(evaluation)
    except Exception as e:
        logger.error(f"CANDIDATE.evaluate | FAILED: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Evaluation failed: {str(e)}")


@router.post("/{candidate_id}/override", response_model=EvaluationResponse)
async def override_evaluation(
    req_id: str,
    candidate_id: str,
    body: OverrideRequest,
    db: AsyncSession = Depends(get_db),
):
    """Override the AI decision with a human decision."""
    logger.info(
        f"CANDIDATE.override | START"
        f" | candidate_id={candidate_id}"
        f" | decision={body.decision}"
    )

    eval_q = await db.execute(
        select(Evaluation).where(Evaluation.candidate_id == candidate_id)
    )
    evaluation = eval_q.scalar_one_or_none()
    if not evaluation:
        raise HTTPException(status_code=404, detail="No evaluation found for this candidate")

    try:
        svc = EvaluationService()
        updated = await svc.override_decision(
            db=db,
            evaluation_id=evaluation.id,
            decision=body.decision,
            reason=body.reason,
            overridden_by=body.overridden_by,
        )
        return _to_evaluation_response(updated)
    except Exception as e:
        logger.error(f"CANDIDATE.override | FAILED: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Override failed: {str(e)}")


@router.get("/{candidate_id}/audit", response_model=list[AuditLogResponse])
async def get_audit_log(
    req_id: str,
    candidate_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Get audit trail for a candidate."""
    result = await db.execute(
        select(AuditLog)
        .where(AuditLog.candidate_id == candidate_id)
        .order_by(AuditLog.created_at.desc())
    )
    return [
        AuditLogResponse(
            id=log.id,
            candidate_id=log.candidate_id,
            action=log.action,
            actor=log.actor,
            details=log.details,
            created_at=log.created_at,
        )
        for log in result.scalars().all()
    ]


@router.delete("/{candidate_id}", status_code=204)
async def delete_candidate(
    req_id: str,
    candidate_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Delete a candidate."""
    candidate = await db.get(Candidate, candidate_id)
    if not candidate or candidate.requisition_id != req_id:
        raise HTTPException(status_code=404, detail="Candidate not found")

    logger.info(f"CANDIDATE.delete | Deleting \"{candidate.name}\" ({candidate_id})")
    await db.delete(candidate)


# ── Helpers ──────────────────────────────────────────────────────────────────


async def _handle_resume_upload(candidate: Candidate, resume: UploadFile) -> Candidate:
    """Save uploaded resume and extract text."""
    ext = Path(resume.filename).suffix.lower()
    if ext not in (".pdf", ".docx", ".doc", ".txt"):
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: {ext}. Supported: .pdf, .docx, .txt",
        )

    contents = await resume.read()
    file_size_kb = len(contents) / 1024
    logger.info(f"CANDIDATE.upload | File received | filename={resume.filename} | size={file_size_kb:.1f}KB")

    if len(contents) > settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024:
        raise HTTPException(status_code=400, detail=f"File too large. Max: {settings.MAX_UPLOAD_SIZE_MB}MB")

    upload_dir = Path(settings.UPLOAD_DIR)
    upload_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4().hex[:12]}_{resume.filename}"
    file_path = upload_dir / filename

    with open(file_path, "wb") as f:
        f.write(contents)

    candidate.resume_filename = resume.filename
    candidate.resume_path = str(file_path)

    try:
        start = time.time()
        candidate.resume_text = extract_text_from_file(str(file_path))
        elapsed = int((time.time() - start) * 1000)
        text_len = len(candidate.resume_text) if candidate.resume_text else 0
        logger.info(f"CANDIDATE.upload | Text extracted | chars={text_len} | {elapsed}ms")
    except Exception as e:
        logger.error(f"CANDIDATE.upload | Text extraction FAILED | {type(e).__name__}: {e}")
        candidate.resume_text = None

    return candidate


def _to_candidate_response(c: Candidate, has_evaluation: bool = False) -> CandidateResponse:
    return CandidateResponse(
        id=c.id, requisition_id=c.requisition_id, name=c.name,
        email=c.email, phone=c.phone, resume_filename=c.resume_filename,
        status=c.status, created_at=c.created_at, updated_at=c.updated_at,
        has_evaluation=has_evaluation,
    )


def _to_candidate_detail(c: Candidate, evaluation=None) -> CandidateDetail:
    eval_resp = _to_evaluation_response(evaluation) if evaluation else None
    return CandidateDetail(
        id=c.id, requisition_id=c.requisition_id, name=c.name,
        email=c.email, phone=c.phone, resume_filename=c.resume_filename,
        resume_text=c.resume_text, resume_structured=c.resume_structured,
        status=c.status, created_at=c.created_at, updated_at=c.updated_at,
        has_evaluation=eval_resp is not None, evaluation=eval_resp,
    )


def _to_evaluation_response(e: Evaluation) -> EvaluationResponse:
    return EvaluationResponse(
        id=e.id, candidate_id=e.candidate_id,
        recommendation=e.recommendation, confidence=e.confidence,
        composite_score=e.composite_score, skill_matches=e.skill_matches,
        experience_assessment=e.experience_assessment, education_assessment=e.education_assessment,
        strengths=e.strengths, gaps=e.gaps, explanation=e.explanation,
        decision_trace=e.decision_trace, suggested_actions=e.suggested_actions,
        override_decision=e.override_decision, override_reason=e.override_reason,
        overridden_by=e.overridden_by, overridden_at=e.overridden_at,
        model_used=e.model_used, processing_time_ms=e.processing_time_ms,
        created_at=e.created_at,
    )
