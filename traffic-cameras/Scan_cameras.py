"""Probe the public highway-traffic-camera site for valid camera ids.

GET https://public.highwaystrafficcameras.co.uk/cctvpublicaccess/html/{id}.html
for ids 0-100000 with 50 threads. A page counts when HTTP 200 and the body
lacks "Camera not available". Image URL pattern:
.../cctvpublicaccess/images/{id}.jpg.

Saves found_cameras.json ([{id, name, viewUrl, imageUrl}]); resumable —
already-seen ids are skipped on re-run.

Usage: python Scan_cameras.py [--start 0] [--end 100000] [--out found_cameras.json]
"""

from __future__ import annotations

import argparse
import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

BASE = "https://public.highwaystrafficcameras.co.uk/cctvpublicaccess"
NOT_AVAILABLE = "Camera not available"
THREADS = 50
TIMEOUT_S = 15


def check_id(session: requests.Session, cam_id: int) -> dict | None:
    url = f"{BASE}/html/{cam_id}.html"
    try:
        resp = session.get(url, timeout=TIMEOUT_S)
    except requests.RequestException:
        return None
    if resp.status_code != 200 or NOT_AVAILABLE in resp.text:
        return None
    m = re.search(r"<title>(.*?)</title>", resp.text, re.S | re.I)
    return {
        "id": cam_id,
        "name": (m.group(1).strip() if m else ""),
        "viewUrl": url,
        "imageUrl": f"{BASE}/images/{cam_id}.jpg",
    }


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--start", type=int, default=0)
    p.add_argument("--end", type=int, default=100000)
    p.add_argument("--out", default="found_cameras.json")
    p.add_argument("--threads", type=int, default=THREADS)
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    out = Path(args.out)
    found: list[dict] = []
    seen: set[int] = set()
    if out.exists():
        found = json.loads(out.read_text())
        seen = {c["id"] for c in found}
    todo = [i for i in range(args.start, args.end + 1) if i not in seen]
    print(f"resuming: {len(seen)} known, {len(todo)} to probe")

    def save():
        out.write_text(json.dumps(found, indent=1))

    session = requests.Session()
    try:
        with ThreadPoolExecutor(max_workers=args.threads) as pool:
            for i, hit in enumerate(
                pool.map(lambda c: check_id(session, c), todo)
            ):
                if hit is not None:
                    found.append(hit)
                    print(f"found {hit['id']} {hit['name']}")
                if (i + 1) % 500 == 0:
                    save()
                    print(f"... {i + 1}/{len(todo)}")
    finally:
        save()
    print(f"done: {len(found)} cameras -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
