import struct
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
MAX_IMAGE_PIXELS = 16_000_000


def _image_dimensions(contents: bytes, content_type: str) -> tuple[int, int] | None:
    """Read image dimensions from common format headers without decoding or re-encoding pixels."""
    if content_type == "image/png" and contents.startswith(b"\x89PNG\r\n\x1a\n") and len(contents) >= 24:
        return struct.unpack(">II", contents[16:24])

    if content_type == "image/jpeg" and contents.startswith(b"\xff\xd8"):
        offset = 2
        while offset + 4 <= len(contents):
            if contents[offset] != 0xFF:
                offset += 1
                continue
            marker = contents[offset + 1]
            offset += 2
            while marker == 0xFF and offset < len(contents):
                marker = contents[offset]
                offset += 1
            if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
                continue
            if offset + 2 > len(contents):
                break
            segment_length = int.from_bytes(contents[offset:offset + 2], "big")
            if segment_length < 2 or offset + segment_length > len(contents):
                break
            if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
                if segment_length >= 7:
                    height, width = struct.unpack(">HH", contents[offset + 3:offset + 7])
                    return width, height
                break
            offset += segment_length

    if content_type == "image/webp" and len(contents) >= 30 and contents[:4] == b"RIFF" and contents[8:12] == b"WEBP":
        chunk, payload = contents[12:16], contents[20:]
        if chunk == b"VP8X" and len(payload) >= 10:
            width = int.from_bytes(payload[4:7], "little") + 1
            height = int.from_bytes(payload[7:10], "little") + 1
            return width, height
        if chunk == b"VP8 " and len(payload) >= 10 and payload[3:6] == b"\x9d\x01\x2a":
            width = int.from_bytes(payload[6:8], "little") & 0x3FFF
            height = int.from_bytes(payload[8:10], "little") & 0x3FFF
            return width, height
        if chunk == b"VP8L" and len(payload) >= 5 and payload[0] == 0x2F:
            b1, b2, b3, b4 = payload[1:5]
            width = 1 + b1 + ((b2 & 0x3F) << 8)
            height = 1 + (b2 >> 6) + (b3 << 2) + ((b4 & 0x0F) << 10)
            return width, height
    return None


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
    dimensions = _image_dimensions(contents, image.content_type or "")
    if dimensions and dimensions[0] * dimensions[1] > MAX_IMAGE_PIXELS:
        raise HTTPException(status_code=413, detail="Image dimensions must be 16 megapixels or smaller.")
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
