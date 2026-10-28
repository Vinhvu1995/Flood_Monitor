from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class IncidentSource(str, Enum):
    NATIONAL_HIGHWAYS = "national_highways"
    TFL = "tfl"
    TRAFFIC_SCOTLAND = "traffic_scotland"
    TRAFFIC_WATCH_NI = "traffic_watch_ni"


class FloodSeverity(str, Enum):
    UNKNOWN = "unknown"
    MINOR = "minor"
    MODERATE = "moderate"
    SEVERE = "severe"
    EXTREME = "extreme"


class Incident(BaseModel):
    """Normalised traffic incident record where cause is flooding."""

    source: IncidentSource
    external_id: str
    occurred_at: datetime | None = None
    reported_at: datetime
    longitude: float | None = None
    latitude: float | None = None
    road_ref: str | None = None
    description: str = ""
    is_flood: bool = False
    flood_severity: FloodSeverity = FloodSeverity.UNKNOWN
    raw: dict[str, Any] = Field(default_factory=dict)

    @property
    def wkt_point(self) -> str | None:
        if self.longitude is not None and self.latitude is not None:
            return f"POINT({self.longitude} {self.latitude})"
        return None

    def to_geojson_feature(self) -> dict:
        geometry = None
        if self.longitude is not None and self.latitude is not None:
            geometry = {"type": "Point", "coordinates": [self.longitude, self.latitude]}
        return {
            "type": "Feature",
            "geometry": geometry,
            "properties": {
                "source": self.source.value,
                "external_id": self.external_id,
                "occurred_at": self.occurred_at.isoformat() if self.occurred_at else None,
                "reported_at": self.reported_at.isoformat(),
                "longitude": self.longitude,
                "latitude": self.latitude,
                "road_ref": self.road_ref,
                "description": self.description,
                "is_flood": self.is_flood,
                "flood_severity": self.flood_severity.value,
            },
        }
