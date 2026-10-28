"""
Stateless, per-cycle rainfall-radar storm-cell detection.

Fetches the freshest RainViewer radar frame, decodes it via radar_palette's
fixed palette classifier, clusters high-rainfall pixels into discrete storm
cells, and returns padded/floored/capped bounding boxes in WGS84.

No cross-cycle memory here — matching detections across cycles into persistent
"sessions" (with a rolling 3h window, reshape-on-move, expiry) is storm_sessions.py's
job. This module's only contract is: given "now", what does the sky look like
right now.

RainViewer's free tier is personal/educational-use only (see their docs) — this
integration is a prototype pending a commercial licensing decision; see project notes.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import io

import numpy as np
import requests
from PIL import Image
from scipy import ndimage

import radar_palette as palette
from bbox_geo import (
    BBox,
    MAX_BBOX_AREA_KM2,
    MAX_BBOX_DIM_KM,
    bbox_area_km2,
    floor_bbox,
    overlaps_uk_land,
    pad_bbox,
    split_cluster,
)
from fetch_flood_alerts import _pixel_to_latlon, tiles_for_region

RAINVIEWER_WEATHER_MAPS_URL = "https://api.rainviewer.com/public/weather-maps.json"
TILE_SIZE = 256
RADAR_ZOOM = 7

UK_BBOX = (60.85, -8.17, 49.96, 1.77)  # max_lat, min_lon, min_lat, max_lon

MIN_CLUSTER_KM2 = 8.0
PAD_KM = 8.0
MIN_BBOX_KM = 15.0


class RadarFetchError(Exception):
    pass


class RadarDecodeFailure(Exception):
    pass


@dataclass(frozen=True)
class StormCellBBox:
    bbox: BBox
    max_band: str             # "BLUE", "HOT", "EXTREME" (rainviewer) or "ASC_MM" (asc)
    mean_severity: float      # relative intensity for cross-source priority weighting, not a fixed unit
    pixel_area_km2: float     # raw high-rainfall pixel area, before pad/floor/cap
    bbox_area_km2: float      # final bbox area after pad/floor/cap
    centroid: tuple[float, float]   # (lat, lon), intensity-weighted
    snow_fraction: float
    detected_at: datetime
    source_frame_path: str
    source: str = "rainviewer"   # "rainviewer" or "asc"
    split_of: str | None = None


def _latest_frame(timeout: float = 15.0) -> tuple[str, datetime]:
    try:
        r = requests.get(RAINVIEWER_WEATHER_MAPS_URL, timeout=timeout)
        r.raise_for_status()
        d = r.json()
    except requests.RequestException as e:
        raise RadarFetchError(f"weather-maps.json fetch failed: {e}") from e

    past = (d.get("radar") or {}).get("past") or []
    if not past:
        raise RadarFetchError("RainViewer returned no past radar frames")
    frame = past[-1]
    return d["host"] + frame["path"], datetime.fromtimestamp(frame["time"], tz=timezone.utc)


def _fetch_tile_png(base_url: str, zoom: int, tx: int, ty: int, timeout: float = 15.0) -> np.ndarray | None:
    url = f"{base_url}/{TILE_SIZE}/{zoom}/{tx}/{ty}/2/0_0.png"
    try:
        r = requests.get(url, timeout=timeout)
    except requests.RequestException:
        return None
    if r.status_code != 200:
        return None
    img = Image.open(io.BytesIO(r.content)).convert("RGBA")
    return np.array(img, dtype=np.uint8)


def _fetch_mosaic(base_url: str, region_bbox: tuple[float, float, float, float], zoom: int):
    """region_bbox is (max_lat, min_lon, min_lat, max_lon), tiles_for_region's order."""
    tiles = tiles_for_region(*region_bbox, zoom)
    xs = sorted({t[0] for t in tiles})
    ys = sorted({t[1] for t in tiles})
    x_min, y_min = xs[0], ys[0]
    width = (xs[-1] - x_min + 1) * TILE_SIZE
    height = (ys[-1] - y_min + 1) * TILE_SIZE
    mosaic = np.zeros((height, width, 4), dtype=np.uint8)

    for tx, ty in tiles:
        arr = _fetch_tile_png(base_url, zoom, tx, ty)
        if arr is None:
            continue
        ox, oy = (tx - x_min) * TILE_SIZE, (ty - y_min) * TILE_SIZE
        mosaic[oy:oy + TILE_SIZE, ox:ox + TILE_SIZE, :] = arr

    return mosaic, x_min, y_min


def _pixel_box_to_bbox(r0: int, r1: int, c0: int, c1: int, x_min: int, y_min: int, zoom: int) -> BBox:
    """r/c are inclusive pixel row/col indices into the mosaic (row=north-south, col=west-east)."""
    lat_n, lon_w = _pixel_to_latlon(c0, r0, x_min, y_min, zoom, extent=TILE_SIZE)
    lat_s, lon_e = _pixel_to_latlon(c1 + 1, r1 + 1, x_min, y_min, zoom, extent=TILE_SIZE)
    return (lon_w, lat_s, lon_e, lat_n)


def detect_storm_cells(
    *,
    region_bbox: tuple[float, float, float, float] = UK_BBOX,
    zoom: int = RADAR_ZOOM,
    min_cluster_km2: float = MIN_CLUSTER_KM2,
    pad_km: float = PAD_KM,
    min_bbox_km: float = MIN_BBOX_KM,
    max_bbox_area_km2: float = MAX_BBOX_AREA_KM2,
    max_bbox_dim_km: float = MAX_BBOX_DIM_KM,
) -> list[StormCellBBox]:
    """
    Fetch the freshest RainViewer frame, decode, cluster, and return storm-cell
    bboxes. Returns [] if nothing exceeds threshold. Raises RadarFetchError on a
    network/API problem and RadarDecodeFailure if >1% of non-transparent pixels
    don't match the known palette (see radar_palette.MAX_UNKNOWN_FRACTION) —
    callers should NOT treat either exception as "no rain", since a fail-open
    default would starve every active session on a RainViewer outage or an
    undetected palette change.
    """
    base_url, frame_time = _latest_frame()
    mosaic, x_min, y_min = _fetch_mosaic(base_url, region_bbox, zoom)

    high_rain, snow, severity, band_code, unknown_n, non_transparent_n = palette.classify_array(mosaic)
    if palette.is_decode_failure(unknown_n, non_transparent_n):
        raise RadarDecodeFailure(
            f"{unknown_n}/{non_transparent_n} non-transparent pixels unclassified "
            f"(>{palette.MAX_UNKNOWN_FRACTION:.0%}) — possible RainViewer palette change"
        )

    if not np.any(high_rain):
        return []

    structure = np.ones((3, 3), dtype=int)
    labeled, n_labels = ndimage.label(high_rain, structure=structure)

    band_names = {2: palette.HOT, 3: palette.EXTREME}
    cells: list[StormCellBBox] = []
    sea_only_skipped = 0

    def _raw_to_bbox(r0, r1, c0, c1):
        return _pixel_box_to_bbox(r0, r1, c0, c1, x_min, y_min, zoom)

    def _padded_to_bbox(r0, r1, c0, c1):
        return pad_bbox(_raw_to_bbox(r0, r1, c0, c1), pad_km)

    for lbl in range(1, n_labels + 1):
        rows, cols = np.where(labeled == lbl)
        # Fitness check must evaluate the PADDED box (what's actually sent to
        # TomTom), not the raw cluster envelope — padding applied after the cap
        # check silently exceeded it (observed live: 10,400-11,600 km2 sessions
        # against a 9,000 km2 cap, causing persistent HTTP 400s on Stage A).
        leaves = split_cluster(rows, cols, _padded_to_bbox, max_bbox_area_km2, max_bbox_dim_km)
        split_group = f"z{zoom}_{x_min}_{y_min}_l{lbl}" if len(leaves) > 1 else None

        for leaf_rows, leaf_cols in leaves:
            r0, r1, c0, c1 = int(leaf_rows.min()), int(leaf_rows.max()), int(leaf_cols.min()), int(leaf_cols.max())
            raw_bbox = _raw_to_bbox(r0, r1, c0, c1)
            pixel_area_km2 = bbox_area_km2(raw_bbox)
            if pixel_area_km2 < min_cluster_km2:
                continue

            final_bbox = floor_bbox(_padded_to_bbox(r0, r1, c0, c1), min_bbox_km)
            if not overlaps_uk_land(final_bbox):
                sea_only_skipped += 1
                continue

            leaf_severity = severity[leaf_rows, leaf_cols]
            leaf_bands = band_code[leaf_rows, leaf_cols]
            leaf_snow_local = snow[max(r0 - 2, 0):r1 + 3, max(c0 - 2, 0):c1 + 3]

            weights = np.clip(leaf_severity, 1.0, None)
            centroid_lat, centroid_lon = _pixel_to_latlon(
                float(np.average(leaf_cols, weights=weights)) + 0.5,
                float(np.average(leaf_rows, weights=weights)) + 0.5,
                x_min, y_min, zoom, extent=TILE_SIZE,
            )

            cells.append(StormCellBBox(
                bbox=final_bbox,
                max_band=band_names.get(int(leaf_bands.max()), palette.BLUE),
                mean_severity=float(np.mean(leaf_severity)),
                pixel_area_km2=pixel_area_km2,
                bbox_area_km2=bbox_area_km2(final_bbox),
                centroid=(centroid_lat, centroid_lon),
                snow_fraction=float(np.mean(leaf_snow_local)) if leaf_snow_local.size else 0.0,
                detected_at=frame_time,
                source_frame_path=base_url,
                split_of=split_group,
            ))

    if sea_only_skipped:
        print(f"    (skipped {sea_only_skipped} sea-only cell(s) — no UK land in bbox)")

    return cells
