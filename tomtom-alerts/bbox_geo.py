"""
Shared bbox geometry helpers used by both storm-cell detectors
(radar_storm_cells.py for RainViewer, asc_rainfall_cells.py for the NFS ASC
grid) so the pad/floor/cap-and-split logic — and the TomTom area cap itself —
has one definition instead of two copies that could drift apart.
"""

import json
import math
import os

BBox = tuple[float, float, float, float]  # min_lon, min_lat, max_lon, max_lat

# UK country boundary (57 polygons incl. Orkney/Shetland/Hebrides/NI/Isle of
# Wight etc., ~datasets/geo-countries on GitHub, Natural-Earth-derived).
# Storm cells that don't touch this at all are open sea with no roads —
# polling them wastes API calls and was observed happening (e.g. a cell over
# the open Atlantic west of the Outer Hebrides, another in the Pentland
# Firth north of mainland Scotland, both on 2026-08-04).
UK_BOUNDARY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uk_boundary.geojson")

_uk_land_geom = None  # lazy-loaded, prepared for fast repeated .intersects()


def _load_uk_land():
    global _uk_land_geom
    if _uk_land_geom is None:
        from shapely.geometry import shape
        from shapely.prepared import prep
        with open(UK_BOUNDARY_PATH) as f:
            d = json.load(f)
        geom = shape(d["features"][0]["geometry"])
        _uk_land_geom = prep(geom)
    return _uk_land_geom


def overlaps_uk_land(bbox: BBox) -> bool:
    """True if bbox touches UK land at all (not necessarily fully on land —
    a coastal storm cell's padded bbox legitimately straddles the coastline;
    only fully-at-sea bboxes should be rejected)."""
    from shapely.geometry import box as shapely_box
    return _load_uk_land().intersects(shapely_box(*bbox))

_KM_PER_DEG_LAT = 110.574
_KM_PER_DEG_LON_AT_EQUATOR = 111.320

# TomTom incidentDetails bbox cap. Inferred from uk_strips_tomtom.geojson's
# empirically-working strips (~9,400-9,500 km2, 205x45 km) — NOT confirmed
# against TomTom's current v5 docs (the one documented formula found is for the
# deprecated v4 zoom-based endpoint and doesn't transfer). Treat as a tunable
# working assumption; fetch_bbox() also bisects reactively on an HTTP 400.
MAX_BBOX_AREA_KM2 = 9000.0
MAX_BBOX_DIM_KM = 200.0


def bbox_dims_km(bbox: BBox) -> tuple[float, float]:
    min_lon, min_lat, max_lon, max_lat = bbox
    mean_lat = (min_lat + max_lat) / 2.0
    width_km = (max_lon - min_lon) * _KM_PER_DEG_LON_AT_EQUATOR * math.cos(math.radians(mean_lat))
    height_km = (max_lat - min_lat) * _KM_PER_DEG_LAT
    return abs(width_km), abs(height_km)


def bbox_area_km2(bbox: BBox) -> float:
    width_km, height_km = bbox_dims_km(bbox)
    return width_km * height_km


def pad_bbox(bbox: BBox, pad_km: float) -> BBox:
    min_lon, min_lat, max_lon, max_lat = bbox
    mean_lat = (min_lat + max_lat) / 2.0
    dlon = pad_km / (_KM_PER_DEG_LON_AT_EQUATOR * math.cos(math.radians(mean_lat)))
    dlat = pad_km / _KM_PER_DEG_LAT
    return (min_lon - dlon, min_lat - dlat, max_lon + dlon, max_lat + dlat)


def floor_bbox(bbox: BBox, min_km: float) -> BBox:
    min_lon, min_lat, max_lon, max_lat = bbox
    width_km, height_km = bbox_dims_km(bbox)
    clon, clat = (min_lon + max_lon) / 2.0, (min_lat + max_lat) / 2.0
    if width_km < min_km:
        half_deg = (min_km / 2.0) / (_KM_PER_DEG_LON_AT_EQUATOR * math.cos(math.radians(clat)))
        min_lon, max_lon = clon - half_deg, clon + half_deg
    if height_km < min_km:
        half_deg = (min_km / 2.0) / _KM_PER_DEG_LAT
        min_lat, max_lat = clat - half_deg, clat + half_deg
    return (min_lon, min_lat, max_lon, max_lat)


def split_cluster(rows, cols, to_bbox, max_area_km2: float = MAX_BBOX_AREA_KM2,
                   max_dim_km: float = MAX_BBOX_DIM_KM, depth: int = 0):
    """
    Recursively bisect a cluster's pixel/cell index set (by row or column,
    whichever span is larger) until each piece's naive bbox fits both caps.
    Recomputes the bounding box from the actual member indices after each
    split, which prunes empty regions automatically (a half with no
    high-rainfall cells never becomes its own leaf).

    `to_bbox(r0, r1, c0, c1) -> BBox` converts an inclusive row/col index box
    into a WGS84 bbox — the two callers differ here (slippy-tile pixel math vs.
    BNG grid-cell math), everything else is shared.

    Returns a list of (rows, cols) leaf index arrays (same array types as input).
    """
    if len(rows) == 0:
        return []

    r0, r1, c0, c1 = int(rows.min()), int(rows.max()), int(cols.min()), int(cols.max())
    bbox = to_bbox(r0, r1, c0, c1)
    width_km, height_km = bbox_dims_km(bbox)

    fits = (width_km * height_km) <= max_area_km2 and width_km <= max_dim_km and height_km <= max_dim_km
    if fits or depth > 8:  # depth cap: pathological shapes stop splitting rather than recurse forever
        return [(rows, cols)]

    if (r1 - r0) >= (c1 - c0):
        mid = (r0 + r1) // 2
        mask_a, mask_b = rows <= mid, rows > mid
    else:
        mid = (c0 + c1) // 2
        mask_a, mask_b = cols <= mid, cols > mid

    leaves = []
    for mask in (mask_a, mask_b):
        if mask.any():
            leaves.extend(split_cluster(rows[mask], cols[mask], to_bbox, max_area_km2, max_dim_km, depth + 1))
    return leaves
