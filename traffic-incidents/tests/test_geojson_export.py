"""Export window: per-run exports cover recent reports only, not full history."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine

import storage.db as dbmod
from models.incident import Incident, IncidentSource
from storage.db import Base, upsert_incidents
from storage.geojson_export import export_geojson


@pytest.fixture()
def seeded_db(monkeypatch, tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(dbmod, "engine", engine)
    import storage.geojson_export as geo

    monkeypatch.setattr(geo, "engine", engine)
    now = datetime(2026, 1, 8, 12, 0, tzinfo=timezone.utc)
    upsert_incidents(
        [
            Incident(
                source=IncidentSource.TFL,
                external_id="recent",
                reported_at=now - timedelta(hours=1),
                is_flood=True,
                longitude=-0.1,
                latitude=51.5,
                description="recent flood",
            ),
            Incident(
                source=IncidentSource.TFL,
                external_id="old",
                reported_at=now - timedelta(hours=48),
                is_flood=True,
                description="old flood",
            ),
            Incident(
                source=IncidentSource.TFL,
                external_id="nonflood",
                reported_at=now - timedelta(minutes=30),
                is_flood=False,
                description="planned closure",
            ),
        ]
    )
    return now


def test_export_without_window_returns_all_flood(seeded_db):
    fc = export_geojson(flood_only=True)
    assert {f["properties"]["external_id"] for f in fc["features"]} == {"recent", "old"}


def test_export_window_excludes_stale_history(seeded_db):
    fc = export_geojson(start=seeded_db - timedelta(hours=6), flood_only=True)
    assert [f["properties"]["external_id"] for f in fc["features"]] == ["recent"]


def test_export_flood_only_excludes_nonflood(seeded_db):
    fc = export_geojson(flood_only=True)
    assert "nonflood" not in {f["properties"]["external_id"] for f in fc["features"]}
    fc_all = export_geojson(flood_only=False)
    assert "nonflood" in {f["properties"]["external_id"] for f in fc_all["features"]}


def test_export_geometry_and_coords(seeded_db):
    fc = export_geojson(start=seeded_db - timedelta(hours=6), flood_only=True)
    (feat,) = fc["features"]
    assert feat["geometry"] == {"type": "Point", "coordinates": [-0.1, 51.5]}
    assert feat["properties"]["latitude"] == 51.5
    assert feat["properties"]["longitude"] == -0.1
