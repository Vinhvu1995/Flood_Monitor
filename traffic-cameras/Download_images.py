"""Legacy continuous downloader of all cameras (superseded by rainfall_trigger).

Downloads every camera every 15 min into uk_highways_cameras/<Road>/<id>/
(road parsed from the description, e.g. M25, A1(M)) and mirrors to S3
Traffic_Cameras/Images/... (region eu-west-2).

Kept for provenance; the live job is rainfall_trigger.py.
"""

from __future__ import annotations

import argparse
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from rainfall_trigger import atomic_write, fetch_image

BASE_S3_PREFIX = "Traffic_Cameras/Images"
S3_REGION_LEGACY = "eu-west-2"
S3_BUCKET = "previsico-modelling-team"
INTERVAL_MIN = 15

# Mirrors the Paris original: re.search(r"([M|A]\d{1,3}(\(M\))?)", IGNORECASE).
# No trailing \b: a word boundary cannot follow ")", so it would drop "(M)".
ROAD_RE = re.compile(r"([MA]\d{1,3}(\(M\))?)", re.IGNORECASE)


def road_from_description(desc: str) -> str:
    m = ROAD_RE.search(desc or "")
    return m.group(1).upper() if m else "Unknown"


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--metadata", default="camera_metadata_complete.csv",
                   help="CSV with Camera_ID, Description_Text, Image_URL, HTML_URL")
    p.add_argument("--out-dir", default="uk_highways_cameras")
    p.add_argument("--s3", action="store_true", help="also mirror to S3")
    p.add_argument("--once", action="store_true", help="single pass then exit")
    return p.parse_args(argv)


def load_targets(metadata_csv: str) -> list[dict]:
    import csv

    with open(metadata_csv, newline="") as fh:
        return [
            {"id": r["Camera_ID"], "desc": r.get("Description_Text", ""),
             "url": r["Image_URL"]}
            for r in csv.DictReader(fh)
            if r.get("Image_URL")
        ]


def s3_put_legacy(key: str, content: bytes) -> None:
    import boto3

    boto3.client("s3", region_name=S3_REGION_LEGACY).put_object(
        Bucket=S3_BUCKET, Key=key, Body=content
    )


def run_once(targets: list[dict], out_dir: Path, mirror_s3: bool) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    for t in targets:
        content = fetch_image(t["url"])
        if content is None:
            print(f"{t['id']}: failed")
            continue
        road = road_from_description(t["desc"])
        rel = f"{road}/{t['id']}/{t['id']}_{stamp}.jpg"
        atomic_write(out_dir / rel, content)
        if mirror_s3:
            s3_put_legacy(f"{BASE_S3_PREFIX}/{rel}", content)
        print(f"{t['id']}: saved {rel}")


def main(argv=None) -> int:
    args = parse_args(argv)
    targets = load_targets(args.metadata)
    out_dir = Path(args.out_dir)
    while True:
        run_once(targets, out_dir, args.s3)
        if args.once:
            return 0
        time.sleep(INTERVAL_MIN * 60)


if __name__ == "__main__":
    raise SystemExit(main())
