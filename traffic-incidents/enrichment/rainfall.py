"""
Rainfall enrichment for flood incidents.
Reads ESRI ASCII raster files from the NFS hourly rainfall directory and
attaches rainfall_latest_mm and rainfall_24h_mm to each incident row.

NFS path (default): /nfs/develop_pff/processing/UK/national/Rainfall/Hourly
File layout: YYYYMMDD/rainfall{idx}.asc  (idx = hours since 02:00 UTC, 1-indexed)
Grid CRS: EPSG:27700 (British National Grid)

The directory comes from ``config.settings`` (RAINFALL_DIR env / .env),
never from a hardcoded constant alone.
"""
from __future__ import annotations

import math
import os
from datetime import datetime, timedelta, timezone

import numpy as np
from pyproj import Transformer

from config.settings import settings

RAINFALL_DIR = settings.rainfall_dir

_WGS84_TO_BNG = Transformer.from_crs("EPSG:4326", "EPSG:27700", always_xy=True)


# ── ASC file resolution ────────────────────────────────────────────────────────

def _asc_path_for_dt(dt: datetime) -> str | None:
    dt_h = dt.replace(minute=0, second=0, microsecond=0)
    base = dt_h.replace(hour=2)
    if dt_h.hour < 2:
        base -= timedelta(days=1)
    idx = int((dt_h - base).total_seconds() / 3600) + 1
    path = os.path.join(RAINFALL_DIR, base.strftime("%Y%m%d"), f"rainfall{idx}.asc")
    return path if os.path.exists(path) else None


def _load_asc(path: str) -> tuple[dict, np.ndarray] | None:
    try:
        hdr: dict = {}
        with open(path) as f:
            for _ in range(6):
                k, v = f.readline().split()
                hdr[k.lower()] = float(v)
        data = np.loadtxt(path, skiprows=6, dtype=np.float32)
        return hdr, data
    except Exception:
        return None


# ── Spatial lookup ────────────────────────────────────────────────────────────

def _point_value(e: float, n: float, hdr: dict, data: np.ndarray) -> float:
    col = int(np.floor((e - hdr["xllcorner"]) / hdr["cellsize"]))
    row = int(np.floor((hdr["yllcorner"] + hdr["nrows"] * hdr["cellsize"] - n) / hdr["cellsize"]))
    if col < 0 or col >= int(hdr["ncols"]) or row < 0 or row >= int(hdr["nrows"]):
        return float("nan")
    v = float(data[row, col])
    return float("nan") if v == hdr["nodata_value"] else v


def _to_bng(lat: float, lon: float) -> tuple[float, float]:
    e, n = _WGS84_TO_BNG.transform(lon, lat)
    return float(e), float(n)


def _mean_val(pts: list[tuple[float, float]], hdr: dict, data: np.ndarray) -> float:
    vals = [_point_value(e, n, hdr, data) for e, n in pts]
    valid = [v for v in vals if not math.isnan(v)]
    return sum(valid) / len(valid) if valid else float("nan")


# ── Public enrichment function ────────────────────────────────────────────────

def enrich(rows: list[dict], now: datetime | None = None) -> list[dict]:
    """
    Add rainfall_latest_mm and rainfall_24h_mm to each row dict.
    Rows must have 'latitude' and 'longitude' keys (float or None).
    Returns a new list with the two columns added (NaN where data unavailable).
    """
    if now is None:
        now = datetime.now(timezone.utc)

    nan = float("nan")

    # Resolve BNG points for each row (None where no geometry)
    bng_pts: list[tuple[float, float] | None] = []
    for row in rows:
        lat, lon = row.get("latitude"), row.get("longitude")
        if lat is not None and lon is not None:
            bng_pts.append(_to_bng(lat, lon))
        else:
            bng_pts.append(None)

    # Latest ASC (current hour)
    latest_path = _asc_path_for_dt(now)
    if not latest_path:
        print(f"  [rainfall] Latest ASC not found under {RAINFALL_DIR} — setting NaN")
        return [{**r, "rainfall_latest_mm": nan, "rainfall_24h_mm": nan} for r in rows]

    latest = _load_asc(latest_path)
    if latest is None:
        print(f"  [rainfall] Failed to load {latest_path} — setting NaN")
        return [{**r, "rainfall_latest_mm": nan, "rainfall_24h_mm": nan} for r in rows]

    latest_hdr, latest_data = latest
    latest_vals = [
        _mean_val([pt], latest_hdr, latest_data) if pt else nan
        for pt in bng_pts
    ]

    # 24h accumulated ASC files
    seen: set[str] = set()
    paths_24h: list[str] = []
    for h in range(23, -1, -1):
        p = _asc_path_for_dt(now - timedelta(hours=h))
        if p and p not in seen:
            paths_24h.append(p)
            seen.add(p)

    loaded = [r for p in paths_24h if (r := _load_asc(p)) is not None]
    print(f"  [rainfall] latest={os.path.basename(latest_path)}  24h files={len(loaded)}/{len(paths_24h)}")

    if not loaded:
        accum_vals = [nan] * len(rows)
    else:
        # Sum all 24h arrays, then sample once per incident
        ref_hdr = loaded[0][0]
        sum_arr = np.zeros(loaded[0][1].shape, dtype=np.float32)
        for hdr, data in loaded:
            sum_arr += np.where(data == hdr["nodata_value"], 0.0, data).astype(np.float32)
        accum_vals = [
            _mean_val([pt], ref_hdr, sum_arr) if pt else nan
            for pt in bng_pts
        ]

    return [
        {**row, "rainfall_latest_mm": latest_vals[i], "rainfall_24h_mm": accum_vals[i]}
        for i, row in enumerate(rows)
    ]


def rainfall_latest_mm(lat: float | None, lon: float | None, now: datetime | None = None) -> float:
    """Latest-hour rainfall in mm at a WGS84 point (NaN where unavailable)."""
    rows = enrich([{"latitude": lat, "longitude": lon}], now=now)
    return rows[0]["rainfall_latest_mm"]


def rainfall_24h_mm(lat: float | None, lon: float | None, now: datetime | None = None) -> float:
    """24-hour accumulated rainfall in mm at a WGS84 point (NaN where unavailable)."""
    rows = enrich([{"latitude": lat, "longitude": lon}], now=now)
    return rows[0]["rainfall_24h_mm"]
