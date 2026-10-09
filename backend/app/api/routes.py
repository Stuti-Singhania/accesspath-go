from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select

from app.config import Settings, get_settings
from app.database import get_session
from app.models.report import BarrierReport
from app.schemas.accessibility import (
    BarrierAnalysis,
    PreferenceRequest,
    ReportCreate,
    ReportRead,
    RouteRequest,
)
from app.services.ai import LocalAIUnavailable, analyze_image, extract_preferences
from app.services.routing import RoutingUnavailable, build_routes

router = APIRouter(prefix="/api")


@router.post("/preferences")
async def preferences(body: PreferenceRequest, settings: Annotated[Settings, Depends(get_settings)]):
    extracted, source, fallback_reason = await extract_preferences(settings, body.text)
    return {"preferences": extracted, "source": source, "fallback_reason": fallback_reason}


@router.post("/routes")
async def routes(
    body: RouteRequest,
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
):
    try:
        reports = session.exec(select(BarrierReport)).all()
        return await build_routes(settings, body, [r.model_dump() for r in reports])
    except RoutingUnavailable as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="Community reports are temporarily unavailable; try again shortly.") from exc


@router.post("/analyze-barrier", response_model=BarrierAnalysis)
async def barrier_analysis(
    image: Annotated[UploadFile, File()],
    settings: Annotated[Settings, Depends(get_settings)],
):
    if image.content_type not in ("image/jpeg", "image/png", "image/webp"):
        raise HTTPException(status_code=415, detail="Upload a JPEG, PNG, or WebP image.")
    contents = await image.read(8 * 1024 * 1024 + 1)
    if len(contents) > 8 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Image must be 8 MB or smaller.")
    if not contents:
        raise HTTPException(status_code=400, detail="The selected image is empty.")
    try:
        return await analyze_image(settings, contents)
    except LocalAIUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/reports", response_model=ReportRead, status_code=201)
def create_report(body: ReportCreate, session: Annotated[Session, Depends(get_session)]):
    try:
        report = BarrierReport(**body.model_dump())
        session.add(report)
        session.commit()
        session.refresh(report)
        return ReportRead(**body.model_dump(), id=report.id, created_at=report.created_at)
    except SQLAlchemyError as exc:
        session.rollback()
        raise HTTPException(status_code=503, detail="The report could not be saved. Please try again.") from exc


@router.get("/reports", response_model=list[ReportRead])
def list_reports(
    session: Annotated[Session, Depends(get_session)],
    min_lon: float | None = Query(default=None, ge=-180, le=180),
    min_lat: float | None = Query(default=None, ge=-90, le=90),
    max_lon: float | None = Query(default=None, ge=-180, le=180),
    max_lat: float | None = Query(default=None, ge=-90, le=90),
):
    bounds = (min_lon, min_lat, max_lon, max_lat)
    if any(value is not None for value in bounds) and (
        any(value is None for value in bounds) or min_lon > max_lon or min_lat > max_lat
    ):
        raise HTTPException(status_code=422, detail="Provide a complete, ordered bounding box.")
    statement = select(BarrierReport).order_by(BarrierReport.created_at.desc())
    if min_lon is not None:
        statement = statement.where(
            BarrierReport.longitude >= min_lon,
            BarrierReport.longitude <= max_lon,
            BarrierReport.latitude >= min_lat,
            BarrierReport.latitude <= max_lat,
        )
    try:
        reports = session.exec(statement).all()
        return [ReportRead.model_validate(r) for r in reports]
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="Community reports are temporarily unavailable.") from exc
