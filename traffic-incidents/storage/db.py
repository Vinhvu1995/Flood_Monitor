"""SQLite/PostGIS storage. Upserts keyed on (source, external_id)."""
from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    select,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from config.settings import settings
from models.incident import Incident

engine = create_engine(settings.database_url, echo=False)


class Base(DeclarativeBase):
    pass


class IncidentRow(Base):
    __tablename__ = "incidents"
    __table_args__ = (UniqueConstraint("source", "external_id", name="uq_source_external_id"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(64))
    external_id: Mapped[str] = mapped_column(String(256))
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    road_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    description: Mapped[str] = mapped_column(Text, default="")
    is_flood: Mapped[bool] = mapped_column(Boolean, default=False)
    flood_severity: Mapped[str] = mapped_column(String(32), default="unknown")
    raw: Mapped[str] = mapped_column(Text, default="{}")


def init_db() -> None:
    Base.metadata.create_all(engine)


def upsert_incidents(incidents: list[Incident]) -> int:
    """Insert new incidents, skip duplicates. Returns count of newly inserted rows."""
    inserted = 0
    with Session(engine) as session:
        for inc in incidents:
            existing = session.scalar(
                select(IncidentRow).where(
                    IncidentRow.source == inc.source.value,
                    IncidentRow.external_id == inc.external_id,
                )
            )
            if existing:
                continue
            row = IncidentRow(
                source=inc.source.value,
                external_id=inc.external_id,
                occurred_at=inc.occurred_at,
                reported_at=inc.reported_at,
                longitude=inc.longitude,
                latitude=inc.latitude,
                road_ref=inc.road_ref,
                description=inc.description,
                is_flood=inc.is_flood,
                flood_severity=inc.flood_severity.value,
                raw=json.dumps(inc.raw),
            )
            session.add(row)
            inserted += 1
        session.commit()
    return inserted
