from datetime import UTC, datetime

from sqlmodel import Field, SQLModel


class BarrierReport(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    latitude: float
    longitude: float
    barrier_type: str
    severity: str
    description: str
    confidence: float
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC), nullable=False)
