"""GeoJSON export of stored incidents, with an optional reported-at window."""
from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from storage.db import IncidentRow, engine


def export_geojson(
    start: datetime | None = None,
    end: datetime | None = None,
    flood_only: bool = True,
) -> dict:
    """Return a GeoJSON FeatureCollection of incidents within an optional date range."""
    with Session(engine) as session:
        stmt = select(IncidentRow)
        if flood_only:
            stmt = stmt.where(IncidentRow.is_flood == True)  # noqa: E712
        if start:
            stmt = stmt.where(IncidentRow.reported_at >= start)
        if end:
            stmt = stmt.where(IncidentRow.reported_at <= end)

        rows = session.scalars(stmt).all()

    features = []
    for row in rows:
        geometry = None
        if row.longitude is not None and row.latitude is not None:
            geometry = {"type": "Point", "coordinates": [row.longitude, row.latitude]}
        features.append(
            {
                "type": "Feature",
                "geometry": geometry,
                "properties": {
                    "id": row.id,
                    "source": row.source,
                    "external_id": row.external_id,
                    "occurred_at": row.occurred_at.isoformat() if row.occurred_at else None,
                    "reported_at": row.reported_at.isoformat(),
                    "longitude": row.longitude,
                    "latitude": row.latitude,
                    "road_ref": row.road_ref,
                    "description": row.description,
                    "is_flood": row.is_flood,
                    "flood_severity": row.flood_severity,
                },
            }
        )
    return {"type": "FeatureCollection", "features": features}


def write_geojson(path: str, **kwargs) -> None:
    fc = export_geojson(**kwargs)
    with open(path, "w") as f:
        json.dump(fc, f, indent=2)
    print(f"Wrote {len(fc['features'])} features to {path}")
