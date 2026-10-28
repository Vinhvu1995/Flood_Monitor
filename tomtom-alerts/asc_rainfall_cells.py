"""
Stateless, per-cycle storm-cell detection from the NFS ASC rainfall nowcast
grid (the same feed uk_incident_monitor.py's Stage C already reads for
post-hoc enrichment — RAINFALL_DIR below is duplicated rather than imported
from uk_incident_monitor to avoid a circular import, matching the existing
pattern of independent constants across this codebase's modules).

This is a second, independent detection source alongside radar_storm_cells.py
(RainViewer): real mm units and an institutionally-proven threshold (the
sibling Traffic_Image_Downloader repo's rainfall_trigger.py uses the same
8.0 mm/hr cutoff), at the cost of hourly refresh and coarser ~2km cells vs.
RainViewer's 10-min/~0.7km. storm_sessions.py unions both sources' detections
each cycle — redundant coverage if either source is degraded (RainViewer's
free tier is personal-use-only; the ASC feed is owned by a different team),
and a real-mm-grounded signal RainViewer's color palette alone can't give.
"""

import os
from datetime import datetime, timedelta

import numpy as np
from pyproj import Transformer
from scipy import ndimage

from bbox_geo import (
    MAX_BBOX_AREA_KM2,
    MAX_BBOX_DIM_KM,
    bbox_area_km2,
    floor_bbox,
    overlaps_uk_land,
    pad_bbox,
    split_cluster,
)
from radar_storm_cells import StormCellBBox

RAINFALL_DIR = "/nfs/develop_pff/processing/UK/national/Rainfall/Hourly"

# Sibling repo's proven threshold (Traffic_Image_Downloader/rainfall_trigger.py:56)
# — reused deliberately for institutional consistency rather than inventing a
# second number for the same underlying feed.
RAINFALL_THRESHOLD_MM = 8.0

MIN_CLUSTER_KM2 = 8.0
PAD_KM = 8.0
MIN_BBOX_KM = 15.0

_BNG_TO_WGS84 = Transformer.from_crs("EPSG:27700", "EPSG:4326", always_xy=True)


class AscDataUnavailable(Exception):
    pass


def _asc_path_for_dt(dt: datetime) -> str | None:
    """Same convention as uk_incident_monitor.py's _asc_path_for_dt: one file
    per forecast hour, `rainfall1` = the hour starting 02:00 UTC that day."""
    dt_h = dt.replace(minute=0, second=0, microsecond=0)
    base = dt_h.replace(hour=2)
    if dt_h.hour < 2:
        base -= timedelta(days=1)
    idx = int((dt_h - base).total_seconds() / 3600) + 1
    path = os.path.join(RAINFALL_DIR, base.strftime("%Y%m%d"), f"rainfall{idx}.asc")
    return path if os.path.exists(path) else None


def _load_asc(path: str) -> tuple[dict, np.ndarray]:
    hdr: dict = {}
    with open(path) as f:
        for _ in range(6):
            k, v = f.readline().split()
            hdr[k.lower()] = float(v)
    return hdr, np.loadtxt(path, skiprows=6, dtype=np.float32)


def _cell_box_to_bng(r0: int, r1: int, c0: int, c1: int, hdr: dict) -> tuple[float, float, float, float]:
    """Inclusive row/col grid-cell indices -> (min_e, min_n, max_e, max_n).
    Row 0 is the northernmost row (standard ESRI ASCII grid convention,
    matching uk_incident_monitor.py's _point_value row calc)."""
    xll, yll, cell, nrows = hdr["xllcorner"], hdr["yllcorner"], hdr["cellsize"], hdr["nrows"]
    min_e = xll + c0 * cell
    max_e = xll + (c1 + 1) * cell
    max_n = yll + nrows * cell - r0 * cell
    min_n = yll + nrows * cell - (r1 + 1) * cell
    return min_e, min_n, max_e, max_n


def _bng_box_to_wgs84_bbox(bng_box: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    min_e, min_n, max_e, max_n = bng_box
    lon1, lat1 = _BNG_TO_WGS84.transform(min_e, min_n)
    lon2, lat2 = _BNG_TO_WGS84.transform(max_e, max_n)
    return (min(lon1, lon2), min(lat1, lat2), max(lon1, lon2), max(lat1, lat2))


def detect_storm_cells_asc(
    now: datetime,
    *,
    threshold_mm: float = RAINFALL_THRESHOLD_MM,
    min_cluster_km2: float = MIN_CLUSTER_KM2,
    pad_km: float = PAD_KM,
    min_bbox_km: float = MIN_BBOX_KM,
    max_bbox_area_km2: float = MAX_BBOX_AREA_KM2,
    max_bbox_dim_km: float = MAX_BBOX_DIM_KM,
) -> list[StormCellBBox]:
    """
    Cluster the latest hourly ASC rainfall grid into storm cells wherever mm
    >= threshold_mm. Returns [] if the grid has no cell above threshold.
    Raises AscDataUnavailable if no ASC file exists for the current hour —
    callers should NOT treat this as "no rain" (same fail-closed contract as
    radar_storm_cells.detect_storm_cells' exceptions).
    """
    path = _asc_path_for_dt(now)
    if path is None:
        raise AscDataUnavailable(f"no ASC file for {now.isoformat()}")

    hdr, data = _load_asc(path)
    nodata = hdr["nodata_value"]
    high_rain = (data != nodata) & (data >= threshold_mm)

    if not np.any(high_rain):
        return []

    structure = np.ones((3, 3), dtype=int)
    labeled, n_labels = ndimage.label(high_rain, structure=structure)

    def _raw_to_bbox(r0, r1, c0, c1):
        return _bng_box_to_wgs84_bbox(_cell_box_to_bng(r0, r1, c0, c1, hdr))

    def _padded_to_bbox(r0, r1, c0, c1):
        return pad_bbox(_raw_to_bbox(r0, r1, c0, c1), pad_km)

    cells: list[StormCellBBox] = []
    sea_only_skipped = 0
    for lbl in range(1, n_labels + 1):
        rows, cols = np.where(labeled == lbl)
        # Fitness check must evaluate the PADDED box — see radar_storm_cells.py's
        # detect_storm_cells for the same fix and why (padding after the cap
        # check silently exceeded it in production).
        leaves = split_cluster(rows, cols, _padded_to_bbox, max_bbox_area_km2, max_bbox_dim_km)
        split_group = f"asc_{os.path.basename(path)}_l{lbl}" if len(leaves) > 1 else None

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

            leaf_mm = data[leaf_rows, leaf_cols]
            mean_mm = float(np.mean(leaf_mm))

            # Normalize onto roughly the same numeric range as RainViewer's HOT
            # band (mean_severity ~1000-1500, see radar_palette.py) so cap
            # enforcement's area*intensity drop-priority tie-break in
            # storm_sessions.py isn't systematically biased against one source.
            # Heuristic, not a unit conversion — the two sources aren't
            # physically comparable, only "roughly similarly weighted".
            severity = 1000.0 + (mean_mm - threshold_mm) * 50.0

            centroid_e = float(np.average(leaf_cols)) * hdr["cellsize"] + hdr["xllcorner"]
            centroid_n = (hdr["yllcorner"] + hdr["nrows"] * hdr["cellsize"]
                          - float(np.average(leaf_rows)) * hdr["cellsize"])
            centroid_lon, centroid_lat = _BNG_TO_WGS84.transform(centroid_e, centroid_n)

            cells.append(StormCellBBox(
                bbox=final_bbox,
                max_band="ASC_MM",
                mean_severity=severity,
                pixel_area_km2=pixel_area_km2,
                bbox_area_km2=bbox_area_km2(final_bbox),
                centroid=(centroid_lat, centroid_lon),
                snow_fraction=0.0,
                detected_at=now,
                source_frame_path=path,
                source="asc",
                split_of=split_group,
            ))

    if sea_only_skipped:
        print(f"    (skipped {sea_only_skipped} sea-only ASC cell(s) — no UK land in bbox)")

    return cells
