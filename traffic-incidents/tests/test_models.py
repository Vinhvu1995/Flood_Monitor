from datetime import datetime, timezone

import pytest

from models.incident import FloodSeverity, Incident, IncidentSource


def _make_incident(**kwargs):
    defaults = dict(
        source=IncidentSource.NATIONAL_HIGHWAYS,
        external_id="test-001",
        reported_at=datetime(2026, 1, 8, 10, 0, tzinfo=timezone.utc),
        is_flood=True,
    )
    defaults.update(kwargs)
    return Incident(**defaults)


def test_wkt_point_with_coords():
    inc = _make_incident(longitude=-1.5, latitude=51.2)
    assert inc.wkt_point == "POINT(-1.5 51.2)"


def test_wkt_point_without_coords():
    inc = _make_incident()
    assert inc.wkt_point is None


def test_geojson_feature_with_geometry():
    inc = _make_incident(longitude=-1.5, latitude=51.2, road_ref="A303", description="Flood")
    feat = inc.to_geojson_feature()
    assert feat["type"] == "Feature"
    assert feat["geometry"]["type"] == "Point"
    assert feat["geometry"]["coordinates"] == [-1.5, 51.2]
    assert feat["properties"]["road_ref"] == "A303"
    assert feat["properties"]["is_flood"] is True


def test_geojson_feature_without_geometry():
    inc = _make_incident()
    feat = inc.to_geojson_feature()
    assert feat["geometry"] is None


def test_default_flood_severity():
    inc = _make_incident()
    assert inc.flood_severity == FloodSeverity.UNKNOWN
