"""Upsert keyed on (source, external_id): inserts are new, replays are skipped."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import storage.db as dbmod
from models.incident import Incident, IncidentSource
from storage.db import Base, IncidentRow, init_db, upsert_incidents


@pytest.fixture()
def isolated_db(monkeypatch, tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(dbmod, "engine", engine)
    # geojson_export holds its own reference to the engine object; rebind it too.
    import storage.geojson_export as geo

    monkeypatch.setattr(geo, "engine", engine)
    return engine


def _inc(source=IncidentSource.TFL, external_id="x-1", **kw):
    base = dict(
        source=source,
        external_id=external_id,
        reported_at=datetime(2026, 1, 8, 10, 0, tzinfo=timezone.utc),
        is_flood=True,
        description="flood",
    )
    base.update(kw)
    return Incident(**base)


def test_upsert_inserts_new(isolated_db):
    assert upsert_incidents([_inc(), _inc(external_id="x-2")]) == 2


def test_upsert_skips_duplicates(isolated_db):
    assert upsert_incidents([_inc()]) == 1
    assert upsert_incidents([_inc()]) == 0  # exact replay inserts nothing
    with Session(isolated_db) as s:
        assert s.query(IncidentRow).count() == 1


def test_upsert_key_includes_source(isolated_db):
    # Same external id from different sources are distinct rows.
    a = _inc(source=IncidentSource.TFL, external_id="same")
    b = _inc(source=IncidentSource.TRAFFIC_SCOTLAND, external_id="same")
    assert upsert_incidents([a, b]) == 2


def test_upsert_does_not_overwrite_existing(isolated_db):
    assert upsert_incidents([_inc(description="first")]) == 1
    assert upsert_incidents([_inc(description="second")]) == 0
    with Session(isolated_db) as s:
        row = s.query(IncidentRow).one()
        assert row.description == "first"


def test_init_db_creates_tables(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path}/fresh.db")
    monkeypatch.setattr(dbmod, "engine", engine)
    init_db()
    with Session(engine) as s:
        assert s.query(IncidentRow).count() == 0
