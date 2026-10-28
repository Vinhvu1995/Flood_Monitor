"""
Fetches flood incidents from TomTom using an adaptive zoom strategy:

  Stage 1 — Initial scan at the region's start zoom.
  Stage 2 — Adaptive drill-down (zoom+1 per iteration, flood tiles only)
             until no new incident IDs appear.
  Stage 3 — Incident Details API (POST by ID) for authoritative metadata.

Can be run standalone or imported and called via run_once() from monitor.py.

Standalone usage:
  REGION=uk python fetch_flood_alerts.py
  REGION=europe+us python fetch_flood_alerts.py

Env vars:
  TOMTOM_API_KEY  — required
  REGION          — uk | europe | us | europe+us  (default: uk)
  MAX_ZOOM        — adaptive drill-down ceiling (default: 14)
"""

from __future__ import annotations

import csv
import json
import math
import os
import threading
import time
from datetime import datetime, timezone

import mapbox_vector_tile
import requests
from dotenv import load_dotenv

try:
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError
    _BOTO3_AVAILABLE = True
except ImportError:
    _BOTO3_AVAILABLE = False

load_dotenv()

FLOOD_CATEGORY       = 11
VECTOR_TILE_URL      = "https://api.tomtom.com/traffic/map/4/tile/incidents/{zoom}/{x}/{y}.pbf"
INCIDENT_DETAILS_URL = "https://api.tomtom.com/traffic/services/5/incidentDetails"
DETAIL_FIELDS = (
    "{incidents{type,geometry{type,coordinates},"
    "properties{id,iconCategory,events{description},from,to,"
    "roadNumbers,magnitudeOfDelay,startTime,endTime,length}}}"
)

CSV_FIELDNAMES = [
    "run_time", "region", "id", "lat", "lon", "from", "to",
    "road_numbers", "magnitude_of_delay", "start_time", "end_time", "description",
]

REGION_PRESETS: dict[str, dict] = {
    "uk": {
        "start_zoom": 8,
        "regions": [("UK", 60.85, -8.17, 49.96, 1.77)],
    },
    "europe": {
        "start_zoom": 6,
        "regions": [("Europe", 72.0, -25.0, 34.0, 45.0)],
    },
    "us": {
        "start_zoom": 6,
        "regions": [
            ("US-contiguous", 49.0, -125.0, 24.0,  -66.0),
            ("US-Alaska",     71.0, -168.0, 54.0, -141.0),
            ("US-Hawaii",     23.0, -160.0, 18.0, -154.0),
        ],
    },
    "europe+us": {
        "start_zoom": 6,
        "regions": [
            ("Europe",        72.0,  -25.0, 34.0,   45.0),
            ("US-contiguous", 49.0, -125.0, 24.0,  -66.0),
            ("US-Alaska",     71.0, -168.0, 54.0, -141.0),
            ("US-Hawaii",     23.0, -160.0, 18.0, -154.0),
        ],
    },
}


# ── Tile math ─────────────────────────────────────────────────────────────────

def _lat_lon_to_tile(lat: float, lon: float, zoom: int) -> tuple[int, int]:
    n = 2 ** zoom
    x = int((lon + 180.0) / 360.0 * n)
    lat_rad = math.radians(lat)
    y = int((1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi) / 2.0 * n)
    return x, y


def _tile_to_bbox(x: int, y: int, zoom: int) -> tuple[float, float, float, float]:
    n = 2 ** zoom
    min_lon = x / n * 360.0 - 180.0
    max_lon = (x + 1) / n * 360.0 - 180.0
    max_lat = math.degrees(math.atan(math.sinh(math.pi * (1.0 - 2.0 * y / n))))
    min_lat = math.degrees(math.atan(math.sinh(math.pi * (1.0 - 2.0 * (y + 1) / n))))
    return min_lon, min_lat, max_lon, max_lat


def _pixel_to_latlon(px: float, py: float, tx: int, ty: int, zoom: int, extent: int = 4096) -> tuple[float, float]:
    n = 2 ** zoom
    lon = (tx + px / extent) / n * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1.0 - 2.0 * (ty + py / extent) / n))))
    return lat, lon


def tiles_for_region(max_lat: float, min_lon: float, min_lat: float, max_lon: float, zoom: int) -> list[tuple[int, int]]:
    x_min, y_min = _lat_lon_to_tile(max_lat, min_lon, zoom)
    x_max, y_max = _lat_lon_to_tile(min_lat, max_lon, zoom)
    return [(x, y) for x in range(x_min, x_max + 1) for y in range(y_min, y_max + 1)]


def child_tiles(tx: int, ty: int) -> list[tuple[int, int]]:
    return [(tx * 2, ty * 2), (tx * 2 + 1, ty * 2), (tx * 2, ty * 2 + 1), (tx * 2 + 1, ty * 2 + 1)]


# ── API calls ─────────────────────────────────────────────────────────────────

def _get(url: str, params: dict, api_key: str, retries: int = 3) -> requests.Response | None:
    params = {**params, "key": api_key}
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, timeout=15)
            if r.status_code == 429:
                wait = 2 ** attempt
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            if attempt == retries - 1:
                print(f"    Warning: {e}")
    return None


def fetch_vector_tile(x: int, y: int, zoom: int, api_key: str) -> bytes | None:
    r = _get(VECTOR_TILE_URL.format(zoom=zoom, x=x, y=y), {}, api_key)
    return r.content if r else None


def fetch_details_by_ids(ids: list[str], api_key: str) -> list[dict]:
    features = []
    for i in range(0, len(ids), 100):
        batch = ids[i:i + 100]
        try:
            r = requests.post(
                INCIDENT_DETAILS_URL,
                params={"key": api_key, "fields": DETAIL_FIELDS, "language": "en-GB"},
                json={"ids": batch},
                timeout=15,
            )
            r.raise_for_status()
            features.extend(r.json().get("incidents", []))
        except requests.RequestException as e:
            print(f"    Warning: detail fetch failed: {e}")
    return features


def fetch_details_by_bbox(min_lon: float, min_lat: float, max_lon: float, max_lat: float, api_key: str) -> list[dict]:
    r = _get(INCIDENT_DETAILS_URL, {
        "bbox": f"{min_lon},{min_lat},{max_lon},{max_lat}",
        "categoryFilter": str(FLOOD_CATEGORY),
        "fields": DETAIL_FIELDS,
        "language": "en-GB",
    }, api_key)
    return r.json().get("incidents", []) if r else []


# ── MVT parsing ───────────────────────────────────────────────────────────────

def _categories(props: dict) -> list:
    """Return all incident categories from a feature (clusters carry icon_category_0, _1, _2 ...)."""
    cats = []
    i = 0
    while f"icon_category_{i}" in props:
        cats.append(props[f"icon_category_{i}"])
        i += 1
    if not cats:
        for key in ("icon_category", "iconCategory"):
            if key in props:
                return [props[key]]
    return cats


def _category(props: dict):
    cats = _categories(props)
    return cats[0] if cats else None


def _incident_id(props: dict, feature_id) -> str:
    for key in ("id", "incident_id"):
        if props.get(key):
            return str(props[key])
    return str(feature_id) if feature_id else ""


def parse_flood_incidents(pbf: bytes, tx: int, ty: int, zoom: int) -> list[dict]:
    try:
        decoded = mapbox_vector_tile.decode(pbf)
    except Exception:
        return []

    results = []
    for layer in decoded.values():
        for feature in layer.get("features", []):
            props = feature.get("properties", {})
            if FLOOD_CATEGORY not in _categories(props):
                continue
            inc_id = _incident_id(props, feature.get("id"))
            geom   = feature.get("geometry", {})
            gtype  = geom.get("type", "")
            raw    = geom.get("coordinates", [])
            if gtype == "Point" and raw:
                lat, lon = _pixel_to_latlon(raw[0], raw[1], tx, ty, zoom)
                coords = [lon, lat]
            elif gtype == "LineString" and raw:
                coords = [list(reversed(_pixel_to_latlon(c[0], c[1], tx, ty, zoom))) for c in raw]
            else:
                coords = []
            results.append({
                "id": inc_id, "tile": (tx, ty),
                "geometry": {"type": gtype, "coordinates": coords},
                "properties": props,
            })
    return results


def scan_tiles(tile_list: list[tuple[int, int]], zoom: int, api_key: str) -> tuple[dict, set]:
    seen: dict[str, dict] = {}
    flood_tiles: set[tuple[int, int]] = set()
    for tx, ty in tile_list:
        pbf = fetch_vector_tile(tx, ty, zoom, api_key)
        if pbf:
            for inc in parse_flood_incidents(pbf, tx, ty, zoom):
                flood_tiles.add(inc["tile"])
                if inc["id"] and inc["id"] not in seen:
                    seen[inc["id"]] = inc
        time.sleep(0.05)
    return seen, flood_tiles


def adaptive_drill_down(
    flood_tiles: set[tuple[int, int]],
    current_zoom: int,
    known_ids: set[str],
    api_key: str,
    max_zoom: int,
) -> tuple[dict[str, dict], int]:
    all_seen: dict[str, dict] = {}
    active_tiles = set(flood_tiles)

    while current_zoom < max_zoom:
        next_zoom  = current_zoom + 1
        next_tiles = list(dict.fromkeys(
            child for tx, ty in active_tiles for child in child_tiles(tx, ty)
        ))
        new_seen, new_flood_tiles = scan_tiles(next_tiles, next_zoom, api_key)
        newly_found = {k: v for k, v in new_seen.items() if k not in known_ids}

        if not newly_found:
            break

        known_ids.update(newly_found)
        all_seen.update(newly_found)
        active_tiles  = new_flood_tiles
        current_zoom  = next_zoom

    return all_seen, current_zoom


# ── Output paths ──────────────────────────────────────────────────────────────

def _output_paths(region: str, run_time: str, output_dir: str) -> tuple[str, str, str]:
    """
    Return (date_dir, csv_path, geojson_path) for a given run.

    Local layout:
      <output_dir>/YYYYMMDD/flood_alerts_YYYYMMDD.csv
      <output_dir>/YYYYMMDD/<region>_YYYYMMDDTHHMMSSz.geojson
    """
    date_str    = run_time[:10].replace("-", "")            # 20260424
    ts_str      = run_time[11:19].replace(":", "") + "Z"    # 102530Z
    safe_region = region.replace("+", "_")                  # europe_us
    date_dir    = os.path.join(output_dir, date_str)
    csv_path    = os.path.join(date_dir, f"flood_alerts_{date_str}.csv")
    geojson_path = os.path.join(date_dir, f"{safe_region}_{date_str}T{ts_str}.geojson")
    return date_dir, csv_path, geojson_path


def _s3_key(region: str, run_time: str, s3_prefix: str, suffix: str) -> str:
    """Build S3 key: <s3_prefix>/YYYYMMDD/<region>_YYYYMMDDTHHMMSSz.<suffix>"""
    date_str    = run_time[:10].replace("-", "")
    ts_str      = run_time[11:19].replace(":", "") + "Z"
    safe_region = region.replace("+", "_")
    prefix      = s3_prefix.rstrip("/")
    if suffix == "csv":
        return f"{prefix}/{date_str}/flood_alerts_{date_str}.csv"
    return f"{prefix}/{date_str}/{safe_region}_{date_str}T{ts_str}.geojson"


# ── Output writers ────────────────────────────────────────────────────────────

def _representative_point(geometry: dict) -> tuple[float | None, float | None]:
    gtype  = geometry.get("type")
    coords = geometry.get("coordinates", [])
    if gtype == "Point" and coords:
        return coords[1], coords[0]
    if gtype == "LineString" and coords:
        mid = coords[len(coords) // 2]
        return mid[1], mid[0]
    return None, None


def _append_csv(incidents: list[dict], region: str, run_time: str, path: str) -> None:
    file_exists = os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES)
        if not file_exists:
            writer.writeheader()
        for feature in incidents:
            props    = feature.get("properties", {})
            lat, lon = _representative_point(feature.get("geometry", {}))
            writer.writerow({
                "run_time":           run_time,
                "region":             region,
                "id":                 props.get("id", ""),
                "lat":                lat,
                "lon":                lon,
                "from":               props.get("from", ""),
                "to":                 props.get("to", ""),
                "road_numbers":       ", ".join(props.get("roadNumbers", [])),
                "magnitude_of_delay": props.get("magnitudeOfDelay", ""),
                "start_time":         props.get("startTime", ""),
                "end_time":           props.get("endTime", ""),
                "description":        "; ".join(e.get("description", "") for e in props.get("events", [])),
            })


def _write_geojson(incidents: list[dict], path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"type": "FeatureCollection", "features": incidents}, f, indent=2, ensure_ascii=False)


# ── S3 upload ─────────────────────────────────────────────────────────────────

def _upload_to_s3(bucket: str, key: str, body: bytes, content_type: str) -> None:
    if not _BOTO3_AVAILABLE:
        print("    Warning: boto3 not installed — skipping S3 upload")
        return
    try:
        boto3.client("s3").put_object(Bucket=bucket, Key=key, Body=body, ContentType=content_type)
        print(f"    S3: s3://{bucket}/{key}")
    except (BotoCoreError, ClientError) as e:
        print(f"    Warning: S3 upload failed ({key}): {e}")


# ── Core run function (importable) ────────────────────────────────────────────

def run_once(
    region_key: str,
    api_key: str,
    max_zoom: int = 14,
    output_dir: str = "flood_alerts",
    lock: threading.Lock | None = None,
    s3_bucket: str | None = None,
    s3_prefix: str = "flood_alerts",
) -> int:
    """
    Run one full scan cycle for the given region.
    Returns the number of incidents found.
    Writes to <output_dir>/YYYYMMDD/ locally and mirrors to S3 if s3_bucket is set.
    S3 keys are rooted at s3_prefix (e.g. 'Tomtom-alerts').
    """
    config = REGION_PRESETS.get(region_key)
    if not config:
        raise ValueError(f"Unknown region '{region_key}'. Choose: {', '.join(REGION_PRESETS)}")

    start_zoom = config["start_zoom"]
    run_time   = datetime.now(timezone.utc).isoformat()

    # Stage 1: initial scan
    initial_tiles = list(dict.fromkeys(
        tile
        for _, *bbox in config["regions"]
        for tile in tiles_for_region(*bbox, start_zoom)
    ))
    seen_ids, flood_tiles = scan_tiles(initial_tiles, start_zoom, api_key)

    if not flood_tiles:
        return 0

    # Stage 2: adaptive drill-down
    extra, final_zoom = adaptive_drill_down(flood_tiles, start_zoom, set(seen_ids), api_key, max_zoom)
    seen_ids.update(extra)

    # Stage 3: fetch authoritative metadata
    detailed = fetch_details_by_ids(list(seen_ids.keys()), api_key)

    if not detailed:
        for tx, ty in {inc["tile"] for inc in seen_ids.values()}:
            for feature in fetch_details_by_bbox(*_tile_to_bbox(tx, ty, final_zoom), api_key):
                fid = feature.get("properties", {}).get("id", "")
                if fid not in {f.get("properties", {}).get("id") for f in detailed}:
                    detailed.append(feature)
            time.sleep(0.05)

    if not detailed:
        detailed = [
            {"type": "Feature", "geometry": v["geometry"], "properties": v["properties"]}
            for v in seen_ids.values()
        ]

    # Stamp all features with region and run_time
    for feature in detailed:
        feature.setdefault("type", "Feature")
        feature.setdefault("properties", {})
        feature["properties"]["region"]   = region_key
        feature["properties"]["run_time"] = run_time

    # Compute output paths
    date_dir, csv_path, geojson_path = _output_paths(region_key, run_time, output_dir)

    # Write output (thread-safe)
    _lock = lock or threading.Lock()
    with _lock:
        os.makedirs(date_dir, exist_ok=True)
        _append_csv(detailed, region_key, run_time, csv_path)
        _write_geojson(detailed, geojson_path)
        if s3_bucket:
            geojson_key = _s3_key(region_key, run_time, s3_prefix, "geojson")
            csv_key     = _s3_key(region_key, run_time, s3_prefix, "csv")
            _upload_to_s3(s3_bucket, geojson_key,
                          json.dumps({"type": "FeatureCollection", "features": detailed},
                                     ensure_ascii=False).encode(),
                          "application/geo+json")
            with open(csv_path, "rb") as f:
                _upload_to_s3(s3_bucket, csv_key, f.read(), "text/csv")

    return len(detailed)


# ── Standalone entry point ────────────────────────────────────────────────────

def main() -> None:
    api_key = os.environ.get("TOMTOM_API_KEY", "")
    if not api_key:
        raise SystemExit("Error: TOMTOM_API_KEY not set. Copy .env.example to .env and add your key.")

    region   = os.environ.get("REGION", "uk").lower()
    max_zoom = int(os.environ.get("MAX_ZOOM", "14"))

    print(f"Running single scan — region={region}, max_zoom={max_zoom}")
    count = run_once(region, api_key, max_zoom=max_zoom)
    if count:
        print(f"Found {count} incident(s). Saved to flood_alerts.csv and flood_alerts.geojson")
    else:
        print("No flood incidents found.")


if __name__ == "__main__":
    main()
