from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class PreferenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=3, max_length=1000)


class WalkPreferences(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mobility_mode: Literal["wheelchair", "walker", "cane", "none"] = "none"
    avoid_stairs: bool = False
    prefer_ramps: bool = False
    avoid_steep_slopes: bool = False
    avoid_unpaved: bool = False
    max_slope: float = Field(default=8, ge=0, le=30)
    target_duration_minutes: int = Field(default=30, ge=5, le=240)


class RouteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: tuple[float, float] = Field(description="[longitude, latitude]")
    destination: tuple[float, float] = Field(description="[longitude, latitude]")
    preferences: WalkPreferences

    @field_validator("start", "destination")
    @classmethod
    def validate_coordinate(cls, value: tuple[float, float]) -> tuple[float, float]:
        if len(value) != 2 or not (-180 <= value[0] <= 180 and -90 <= value[1] <= 90):
            raise ValueError("Coordinates must be [longitude, latitude] within geographic bounds")
        return value


class RouteSummary(BaseModel):
    distance_meters: float
    duration_minutes: float
    score: float
    stairs: str
    known_barriers: int
    accessibility_notes: list[str]
    geometry: dict


class RouteResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    routes: list[RouteSummary]
    recommended_index: int


class BarrierAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")
    barrier_type: Literal[
        "stairs", "blocked_ramp", "pothole", "broken_sidewalk", "steep_slope",
        "narrow_path", "mud", "obstruction", "inaccessible_entrance", "none"
    ]
    severity: Literal["low", "medium", "high"]
    description: str = Field(max_length=500)
    confidence: float = Field(ge=0, le=1)


class ReportCreate(BarrierAnalysis):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class ReportRead(ReportCreate):
    model_config = ConfigDict(extra="forbid", from_attributes=True)
    id: int
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def ensure_timezone(cls, value: datetime) -> datetime:
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value
