#!/usr/bin/env python3
"""
Single-run collector — fetch live flood incidents from all sources, enrich with
rainfall data from the NFS hourly grid, and upload a GeoParquet snapshot to S3.

Suggested crontab (every 2 minutes, FIXED path — the old
``cd /home/user/traffic-incident-live-data`` directory does not exist; the code
lives at /nfs/develop_vinh/traffic-incident-live-data):

    */2 * * * * cd /nfs/develop_vinh/traffic-incident-live-data && /home/vinh/.local/bin/uv run python run.py >> cron.log 2>&1

Only incidents reported within ``export_lookback_hours`` (default 6 h) are
exported/uploaded per run — earlier versions re-exported every flood row ever
stored on every run.
"""
import io
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO_ROOT)

from dotenv import load_dotenv
load_dotenv(os.path.join(REPO_ROOT, ".env"))

import boto3
import geopandas as gpd
from botocore.exceptions import BotoCoreError, ClientError
from shapely.geometry import Point

from collectors import national_highways, tfl, traffic_scotland, traffic_watch_ni
from config.settings import settings
from enrichment.rainfall import enrich
from storage.db import init_db, upsert_incidents
from storage.geojson_export import export_geojson

_handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
if settings.log_path:
    # Prefer an NFS path (see settings.log_path); never /tmp or the root disk.
    _handlers.append(
        RotatingFileHandler(
            settings.log_path,
            maxBytes=settings.log_max_bytes,
            backupCount=settings.log_backup_count,
        )
    )
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=_handlers,
)
log = logging.getLogger(__name__)

S3_BUCKET = "previsico-modelling-team"
S3_REGION = "eu-west-3"
S3_PREFIX = "Traffic_Incidents/Flood_Incidents"

COLLECTORS = [
    ("national_highways", national_highways.fetch),
    ("tfl", tfl.fetch),
    ("traffic_scotland", traffic_scotland.fetch),
    ("traffic_watch_ni", traffic_watch_ni.fetch),
]


def geojson_to_geoparquet(geojson: dict) -> bytes:
    rows = []
    for f in geojson["features"]:
        props = f["properties"].copy()
        geom = f.get("geometry")
        props["geometry"] = Point(geom["coordinates"]) if geom else None
        rows.append(props)

    gdf = gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326")
    buf = io.BytesIO()
    gdf.to_parquet(buf, index=False)
    return buf.getvalue()


def upload_to_s3(parquet_bytes: bytes, ts: str) -> None:
    s3_key = f"{S3_PREFIX}/{ts[:10]}/{ts}/flood_incidents_{ts}.parquet"
    try:
        s3 = boto3.client("s3", region_name=S3_REGION)
        s3.put_object(
            Bucket=S3_BUCKET,
            Key=s3_key,
            Body=parquet_bytes,
            ContentType="application/octet-stream",
        )
        log.info("Uploaded to s3://%s/%s", S3_BUCKET, s3_key)
    except (BotoCoreError, ClientError) as exc:
        log.error("S3 upload failed: %s", exc)


def main() -> int:
    init_db()
    had_error = False

    for name, fetch_fn in COLLECTORS:
        try:
            incidents = fetch_fn()
            inserted = upsert_incidents(incidents)
            log.info("[%s] %d fetched, %d new", name, len(incidents), inserted)
        except Exception as exc:
            log.error("[%s] failed: %s", name, exc)
            had_error = True

    now = datetime.now(timezone.utc)
    # Per-run export window: only recently reported incidents, not the whole
    # history ever stored.
    start = now - timedelta(hours=settings.export_lookback_hours)
    geojson = export_geojson(start=start, flood_only=True)
    if not geojson["features"]:
        log.info("No recent flood incidents — skipping enrichment and S3 upload")
        return 1 if had_error else 0

    # Extract flat rows for rainfall enrichment
    rows = [
        {
            "latitude": f["properties"].get("latitude") or (f["geometry"]["coordinates"][1] if f["geometry"] else None),
            "longitude": f["properties"].get("longitude") or (f["geometry"]["coordinates"][0] if f["geometry"] else None),
            **f["properties"],
        }
        for f in geojson["features"]
    ]
    enriched_rows = enrich(rows, now=now)

    # Rebuild GeoJSON features with enriched properties
    for feat, enriched in zip(geojson["features"], enriched_rows):
        feat["properties"]["rainfall_latest_mm"] = enriched["rainfall_latest_mm"]
        feat["properties"]["rainfall_24h_mm"] = enriched["rainfall_24h_mm"]

    ts = now.strftime("%Y-%m-%dT%H-%M-%SZ")
    parquet_bytes = geojson_to_geoparquet(geojson)
    upload_to_s3(parquet_bytes, ts)
    enriched_ok = bool(enriched_rows) and enriched_rows[0].get("rainfall_latest_mm") is not None
    log.info(
        "Exported %d flood incidents as GeoParquet (rainfall enriched: %s)",
        len(geojson["features"]),
        "yes" if enriched_ok else "no",
    )

    return 1 if had_error else 0


if __name__ == "__main__":
    sys.exit(main())
