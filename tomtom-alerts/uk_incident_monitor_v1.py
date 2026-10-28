"""
UK Incident Monitor — Incident Details API + adaptive vector tile scan
=======================================================================
Single-run script: performs one Stage A + Stage B pass and exits.
Schedule via cron (e.g. */30 * * * *).

  Stage A — Incident Details API (41 strip bboxes):
    Calls /incidentDetails once per strip, deduplicates by ID,
    saves flooding + unknown incidents only.

  Stage B — Adaptive vector tile scan (zoom 6 → up to zoom 14):
    Drills cluster tiles; flood precision to zoom 18 (1 m BNG threshold).
    Saves flooding + unknown incidents only.

Output per run in OUTPUT_DIR/YYYY-MM-DD/:
  uk_details_<ts>.parquet    — Stage A (flood + unknown)
  uk_vector_<ts>.parquet     — Stage B (flood + unknown)

Usage:
  uv run python uk_incident_monitor.py

Env:
  TOMTOM_API_KEY   — required (read from .env)
  OUTPUT_DIR       — output root directory  (default: uk_incident_runs)
"""

import json
import math
import os
import time
from datetime import datetime, timezone

import boto3
import geopandas as gpd
import mapbox_vector_tile
import requests
from dotenv import load_dotenv
from pyproj import Transformer
from shapely.geometry import LineString, Point, shape

from fetch_flood_alerts import (
    _categories,
    _lat_lon_to_tile,
    _pixel_to_latlon,
    child_tiles,
    fetch_vector_tile,
    tiles_for_region,
)

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────

API_KEY        = os.environ["TOMTOM_API_KEY"]
STRIPS_PATH    = "uk_strips_tomtom.geojson"
OUTPUT_DIR     = os.environ.get("OUTPUT_DIR", "uk_incident_runs")

S3_BUCKET      = "previsico-modelling-team"
S3_REGION      = "eu-west-3"
S3_PREFIX      = "TomTomAlerts/"

# Stage A — Incident Details API
FLOOD_CATEGORY       = 11
UNKNOWN_CATEGORY     = 0
CLUSTER_CATEGORY     = 13
INCIDENT_DETAILS_URL = "https://api.tomtom.com/traffic/services/5/incidentDetails"
ALL_FIELDS = (
    "{incidents{type,geometry{type,coordinates},"
    "properties{id,iconCategory,magnitudeOfDelay,length,"
    "events{description,code,iconCategory},"
    "from,to,roadNumbers,startTime,endTime"
    "}}}"
)

# Stage B — vector tile adaptive scan
UK_BBOX              = (60.85, -8.17, 49.96, 1.77)   # max_lat, min_lon, min_lat, max_lon
VECTOR_START_ZOOM    = 6
VECTOR_MAX_ZOOM      = 14    # max zoom for cluster resolution
FLOOD_PRECISION_ZOOM = 18    # Birmingham/Manchester serve data to z17-18; z19+ universally empty
FLOOD_STABLE_METRES  = 1.0   # stop flood drill when movement < this (BNG metres)

CATEGORY_NAMES = {
    0:  "Unknown",           1:  "Accident",
    2:  "Fog",               3:  "Dangerous Conditions",
    4:  "Rain",              5:  "Ice",
    6:  "Jam",               7:  "Lane Closed",
    8:  "Road Closed",       9:  "Road Works",
    10: "Wind",              11: "Flooding",
    12: "Detour",            13: "Cluster",
    14: "Broken Down Vehicle",
}

# EPSG:4326 → EPSG:27700 (British National Grid)
_WGS84_TO_BNG = Transformer.from_crs("EPSG:4326", "EPSG:27700", always_xy=True)


# ── Geometry helpers ──────────────────────────────────────────────────────────

def _to_bng(lat: float, lon: float) -> tuple[float, float]:
    e, n = _WGS84_TO_BNG.transform(lon, lat)
    return e, n


def _bng_dist(e1: float, n1: float, e2: float, n2: float) -> float:
    return math.sqrt((e2 - e1) ** 2 + (n2 - n1) ** 2)


def _tile_representative(geom: dict, tx: int, ty: int, zoom: int) -> tuple[float | None, float | None]:
    """Return (lat, lon) of the midpoint of a vector tile geometry."""
    gtype  = geom.get("type", "")
    coords = geom.get("coordinates", [])
    if gtype == "Point" and coords:
        return _pixel_to_latlon(coords[0], coords[1], tx, ty, zoom)
    if gtype == "LineString" and coords:
        mid = coords[len(coords) // 2]
        return _pixel_to_latlon(mid[0], mid[1], tx, ty, zoom)
    return None, None


def _tile_to_shapely(geom: dict, tx: int, ty: int, zoom: int):
    """Convert vector tile geometry to a shapely object (WGS-84)."""
    gtype  = geom.get("type", "")
    coords = geom.get("coordinates", [])
    if gtype == "Point" and coords:
        lat, lon = _pixel_to_latlon(coords[0], coords[1], tx, ty, zoom)
        return Point(lon, lat)
    if gtype == "LineString" and len(coords) >= 2:
        pts = [tuple(reversed(_pixel_to_latlon(c[0], c[1], tx, ty, zoom))) for c in coords]
        return LineString(pts)
    return None


# ── Vector tile parsing ───────────────────────────────────────────────────────

def _parse_pbf(pbf: bytes, tx: int, ty: int, zoom: int) -> tuple[dict[str, dict], bool]:
    """
    Decode one PBF tile.
    Returns (incidents_dict, has_cluster).
      incidents_dict: fid → {fid, zoom, category, lat, lon, bng_e, bng_n, geometry, props}
    Clusters are not included in incidents_dict but trigger has_cluster=True.
    """
    try:
        decoded = mapbox_vector_tile.decode(pbf)
    except Exception:
        return {}, False

    incidents: dict[str, dict] = {}
    has_cluster = False

    for layer in decoded.values():
        for feat in layer.get("features", []):
            props = feat.get("properties", {})
            cats  = _categories(props)
            if not cats:
                continue

            cat = cats[0]
            fid = str(props.get("clustered") or feat.get("id") or "")

            if cat == CLUSTER_CATEGORY:
                has_cluster = True
                continue

            lat, lon = _tile_representative(feat.get("geometry", {}), tx, ty, zoom)
            if lat is None:
                continue

            e, n = _to_bng(lat, lon)
            geom = _tile_to_shapely(feat.get("geometry", {}), tx, ty, zoom)

            if fid:
                incidents[fid] = {
                    "fid":      fid,
                    "zoom":     zoom,
                    "category": cat,
                    "lat": lat, "lon": lon,
                    "bng_e": e, "bng_n": n,
                    "geometry": geom,
                    "props":    props,
                }

    return incidents, has_cluster


# ── Stage B: adaptive vector tile scan ───────────────────────────────────────

def vector_adaptive_scan(api_key: str) -> tuple[list[dict], int]:
    """
    Adaptive vector tile scan of the UK starting at zoom 6.

    Phase 1 — cluster drill:
      Repeatedly replace cluster tiles with their 4 children until no cluster
      tiles remain or VECTOR_MAX_ZOOM is reached.
      Keeps the highest-zoom version of each incident.

    Phase 2 — flood precision:
      For each flood found, zooms in one level at a time, checking the tile
      containing the flood's last known location (plus 4 neighbours).
      Stops when the BNG representative point moves < FLOOD_STABLE_METRES
      between successive zooms, or FLOOD_PRECISION_ZOOM is reached.

    Returns (incident_list, total_api_calls).
    """
    calls = 0
    best: dict[str, dict] = {}   # fid → best incident seen so far

    # ── Phase 1: cluster drill-down ───────────────────────────────────────────
    active_tiles = set(tiles_for_region(*UK_BBOX, VECTOR_START_ZOOM))
    zoom = VECTOR_START_ZOOM

    while zoom <= VECTOR_MAX_ZOOM and active_tiles:
        cluster_tiles: set[tuple[int, int]] = set()
        new_count = 0

        for tx, ty in sorted(active_tiles):
            pbf = fetch_vector_tile(tx, ty, zoom, api_key)
            calls += 1
            if not pbf:
                time.sleep(0.05)
                continue

            incidents, has_cluster = _parse_pbf(pbf, tx, ty, zoom)

            if has_cluster:
                cluster_tiles.add((tx, ty))

            for fid, inc in incidents.items():
                if fid not in best or zoom > best[fid]["zoom"]:
                    if fid not in best:
                        new_count += 1
                    best[fid] = inc

            time.sleep(0.05)

        n_floods = sum(1 for inc in best.values() if inc["category"] == FLOOD_CATEGORY)
        print(
            f"    z{zoom}: {len(active_tiles):>4} tiles  "
            f"{len(cluster_tiles):>3} clusters  "
            f"{new_count:>4} new incidents  "
            f"{n_floods} floods"
        )

        if not cluster_tiles:
            break

        active_tiles = {
            child for tx, ty in cluster_tiles for child in child_tiles(tx, ty)
        }
        zoom += 1

    # ── Phase 2: flood precision drill ────────────────────────────────────────
    flood_fids = [fid for fid, inc in best.items() if inc["category"] == FLOOD_CATEGORY]

    if flood_fids:
        print(f"    Flood precision drill: {len(flood_fids)} flood(s) found")

    for fid in flood_fids:
        inc   = best[fid]
        z     = inc["zoom"]
        prev_e, prev_n = inc["bng_e"], inc["bng_n"]

        while z < FLOOD_PRECISION_ZOOM:
            z += 1
            tx, ty = _lat_lon_to_tile(inc["lat"], inc["lon"], z)

            # Check the tile and its 4 immediate neighbours
            candidate_tiles = [(tx, ty), (tx-1, ty), (tx+1, ty), (tx, ty-1), (tx, ty+1)]
            matched = None

            for ctx, cty in candidate_tiles:
                pbf = fetch_vector_tile(ctx, cty, z, api_key)
                calls += 1
                if not pbf:
                    time.sleep(0.05)
                    continue

                incidents, _ = _parse_pbf(pbf, ctx, cty, z)
                time.sleep(0.05)

                # Prefer ID match; fall back to nearest flood within 500 m
                if fid in incidents:
                    matched = incidents[fid]
                    break

                for other in incidents.values():
                    if other["category"] != FLOOD_CATEGORY:
                        continue
                    if _bng_dist(prev_e, prev_n, other["bng_e"], other["bng_n"]) < 500:
                        matched = other
                        break

                if matched:
                    break

            if matched is None:
                break   # incident vanished from tiles at this zoom

            dist = _bng_dist(prev_e, prev_n, matched["bng_e"], matched["bng_n"])
            best[fid].update({
                "zoom":     z,
                "lat":      matched["lat"],
                "lon":      matched["lon"],
                "bng_e":    matched["bng_e"],
                "bng_n":    matched["bng_n"],
                "geometry": matched["geometry"],
            })
            prev_e, prev_n = matched["bng_e"], matched["bng_n"]

            print(f"      Flood {fid[:16]}… zoom {z:>2}  Δ={dist:.3f} m")

            if dist < FLOOD_STABLE_METRES:
                print(f"      → Stabilised at zoom {z} (Δ < {FLOOD_STABLE_METRES} m)")
                break

    return list(best.values()), calls


# ── Stage A: Incident Details API helpers ─────────────────────────────────────

def load_strip_bboxes(path: str) -> list[tuple[float, float, float, float]]:
    with open(path) as f:
        fc = json.load(f)
    bboxes = []
    for feat in fc["features"]:
        coords = feat["geometry"]["coordinates"][0]
        lons = [c[0] for c in coords]
        lats = [c[1] for c in coords]
        bboxes.append((min(lons), min(lats), max(lons), max(lats)))
    return bboxes


def fetch_bbox(
    min_lon: float, min_lat: float, max_lon: float, max_lat: float,
    api_key: str,
    retries: int = 3,
) -> list[dict]:
    params = {
        "key":      api_key,
        "bbox":     f"{min_lon},{min_lat},{max_lon},{max_lat}",
        "fields":   ALL_FIELDS,
        "language": "en-GB",
    }
    for attempt in range(retries):
        try:
            r = requests.get(INCIDENT_DETAILS_URL, params=params, timeout=15)
            if r.status_code == 429:
                time.sleep(2 ** attempt)
                continue
            r.raise_for_status()
            return r.json().get("incidents", [])
        except requests.RequestException as e:
            if attempt == retries - 1:
                print(f"    Warning: bbox {min_lon:.2f},{min_lat:.2f}→{max_lon:.2f},{max_lat:.2f}: {e}")
    return []


def flatten_incident(feature: dict, run_time: str, strip_id: int) -> dict:
    props  = feature.get("properties", {})
    events = props.get("events", [])
    geom_raw = feature.get("geometry")
    geometry = None
    if geom_raw:
        try:
            g = shape(geom_raw)
            if not g.is_empty:
                geometry = g
        except Exception:
            pass
    return {
        "run_time":           run_time,
        "strip_id":           strip_id,
        "incident_id":        props.get("id", ""),
        "icon_category":      props.get("iconCategory"),
        "category_name":      CATEGORY_NAMES.get(props.get("iconCategory"), str(props.get("iconCategory", ""))),
        "magnitude_of_delay": props.get("magnitudeOfDelay"),
        "length_m":           props.get("length"),
        "from_road":          props.get("from", ""),
        "to_road":            props.get("to", ""),
        "road_numbers":       ", ".join(props.get("roadNumbers") or []),
        "start_time":         props.get("startTime", ""),
        "end_time":           props.get("endTime", ""),
        "event_descriptions": "; ".join(e.get("description", "") for e in events),
        "event_codes":        ", ".join(str(e["code"]) for e in events if e.get("code") is not None),
        "event_categories":   ", ".join(str(e["iconCategory"]) for e in events if e.get("iconCategory") is not None),
        "geometry":           geometry,
    }


RELEVANT_CATEGORIES = {FLOOD_CATEGORY, UNKNOWN_CATEGORY}


def details_api_scan(
    bboxes: list[tuple[float, float, float, float]],
    api_key: str,
    run_time: str,
) -> gpd.GeoDataFrame:
    seen: dict[str, dict] = {}
    for strip_id, (lo, la, hi, ha) in enumerate(bboxes, 1):
        incidents = fetch_bbox(lo, la, hi, ha, api_key)
        new_n = 0
        for feat in incidents:
            fid = feat.get("properties", {}).get("id", "")
            key = fid if fid else f"_anon_{len(seen)}"
            if key not in seen:
                seen[key] = flatten_incident(feat, run_time, strip_id)
                new_n += 1
        print(
            f"  Strip {strip_id:>2}/{len(bboxes)}: "
            f"{len(incidents):>5} returned, {new_n:>4} new  "
            f"| total: {len(seen):,}"
        )
        time.sleep(0.1)

    rows = list(seen.values())
    gdf  = gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326")
    return gdf[gdf["icon_category"].isin(RELEVANT_CATEGORIES)].reset_index(drop=True)


# ── Parquet helpers ───────────────────────────────────────────────────────────

def _ts(run_time: str) -> str:
    return run_time[:10].replace("-", "") + "T" + run_time[11:19].replace(":", "") + "Z"


def save_parquets(
    details_gdf: gpd.GeoDataFrame,
    vector_rows: list[dict],
    run_time: str,
    output_dir: str,
) -> tuple[str, str]:
    day_dir = os.path.join(output_dir, run_time[:10])
    os.makedirs(day_dir, exist_ok=True)
    ts = _ts(run_time)

    details_path = os.path.join(day_dir, f"uk_details_{ts}.parquet")
    vec_path     = os.path.join(day_dir, f"uk_vector_{ts}.parquet")

    details_gdf.to_parquet(details_path, index=False)

    relevant_rows = [r for r in vector_rows if r["category"] in RELEVANT_CATEGORIES]
    if relevant_rows:
        vec_records = [
            {
                "run_time":      run_time,
                "incident_id":   r["fid"],
                "icon_category": r["category"],
                "category_name": CATEGORY_NAMES.get(r["category"], str(r["category"])),
                "zoom":          r["zoom"],
                "lat":           r["lat"],
                "lon":           r["lon"],
                "bng_e":         r["bng_e"],
                "bng_n":         r["bng_n"],
                "geometry":      r["geometry"],
            }
            for r in relevant_rows
        ]
        vec_gdf = gpd.GeoDataFrame(vec_records, geometry="geometry", crs="EPSG:4326")
    else:
        vec_gdf = gpd.GeoDataFrame(
            columns=["run_time", "incident_id", "icon_category",
                     "category_name", "zoom", "lat", "lon", "bng_e", "bng_n", "geometry"],
            geometry="geometry", crs="EPSG:4326",
        )

    vec_gdf.to_parquet(vec_path, index=False)
    return details_path, vec_path


# ── S3 upload ─────────────────────────────────────────────────────────────────

def upload_to_s3(local_paths: list[str]) -> None:
    s3 = boto3.client("s3", region_name=S3_REGION)
    for path in local_paths:
        # Mirror local structure: uk_incident_runs/YYYY-MM-DD/file.parquet
        # → TomTomAlerts/YYYY-MM-DD/file.parquet
        rel      = os.path.relpath(path, OUTPUT_DIR)
        s3_key   = S3_PREFIX + rel.replace(os.sep, "/")
        try:
            s3.upload_file(path, S3_BUCKET, s3_key)
            print(f"  ↑ s3://{S3_BUCKET}/{s3_key}")
        except Exception as e:
            print(f"  ✗ S3 upload failed for {os.path.basename(path)}: {e}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    bboxes   = load_strip_bboxes(STRIPS_PATH)
    run_time = datetime.now(timezone.utc).isoformat()
    t0       = time.monotonic()

    print(f"UK Incident Monitor  —  {run_time}")
    print(f"  Details API : {len(bboxes)} strips  ({STRIPS_PATH})")
    print(f"  Vector tiles: zoom {VECTOR_START_ZOOM}→{VECTOR_MAX_ZOOM} adaptive, flood precision → zoom {FLOOD_PRECISION_ZOOM}")
    print(f"  Saving      : flood + unknown only  →  {OUTPUT_DIR}/{run_time[:10]}/\n")

    # Stage A
    print(f"[A] Incident Details API ({len(bboxes)} strips)...")
    details_gdf = details_api_scan(bboxes, API_KEY, run_time)
    details_calls = len(bboxes)

    # Stage B
    print(f"\n[B] Vector tile adaptive scan (zoom {VECTOR_START_ZOOM} → {VECTOR_MAX_ZOOM})...")
    vector_rows, vec_calls = vector_adaptive_scan(API_KEY)

    # Save locally
    details_path, vec_path = save_parquets(details_gdf, vector_rows, run_time, OUTPUT_DIR)

    elapsed        = time.monotonic() - t0
    n_vec_relevant = sum(1 for r in vector_rows if r["category"] in RELEVANT_CATEGORIES)
    n_vec_floods   = sum(1 for r in vector_rows if r["category"] == FLOOD_CATEGORY)
    total_calls    = details_calls + vec_calls

    print(f"\n── Results ──────────────────────────────────────────")
    print(f"  [A] API calls          : {details_calls}  ({len(bboxes)} strips)")
    print(f"  [A] Saved incidents    : {len(details_gdf):,}  (flood + unknown)  →  {details_path}")
    print(f"  [B] API calls          : {vec_calls}")
    print(f"  [B] Saved incidents    : {n_vec_relevant:,}  (flood + unknown)  →  {vec_path}")
    print(f"  [B] Floods found       : {n_vec_floods}")
    print(f"  Total API calls        : {total_calls}")
    print(f"  Run duration           : {elapsed:.1f}s")

    # Upload to S3
    print(f"\n── S3 upload ─────────────────────────────────────────")
    upload_to_s3([details_path, vec_path])


if __name__ == "__main__":
    main()
