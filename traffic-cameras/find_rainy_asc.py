"""Find a rainfall ASC file with rain over a camera (test helper).

Scans dated folders for the first ASC with rainfall >= threshold at any
camera and prints a ``--test-asc ... --dry-run`` command for rainfall_trigger.

Usage: python find_rainy_asc.py [--threshold 10]
"""

from __future__ import annotations

import argparse
from pathlib import Path

from rainfall_trigger import (
    GEOJSON_FILE,
    RAINFALL_DIR,
    load_cameras,
    read_asc_value,
)


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--threshold", type=float, default=10.0)
    p.add_argument("--rainfall-dir", default=RAINFALL_DIR)
    p.add_argument("--geojson-file", default=GEOJSON_FILE)
    return p.parse_args(argv)


def find_first_rainy(threshold: float, cams: list[dict], rainfall_dir: str):
    """Yield (asc_path, cam_id, value) for the first rainy ASC found."""
    for day_dir in sorted(Path(rainfall_dir).iterdir()):
        if not day_dir.is_dir():
            continue
        for asc in sorted(day_dir.glob("rainfall*.asc")):
            for cam in cams:
                v = read_asc_value(asc, cam["e"], cam["n"])
                if v is not None and v >= threshold:
                    return asc, cam["id"], v
    return None


def main(argv=None) -> int:
    args = parse_args(argv)
    cams = load_cameras(args.geojson_file)
    hit = find_first_rainy(args.threshold, cams, args.rainfall_dir)
    if hit is None:
        print(f"No ASC with rainfall >= {args.threshold} mm at any camera.")
        return 1
    asc, cam_id, value = hit
    print(f"# {value:.1f} mm at camera {cam_id} in {asc}")
    print(f"python rainfall_trigger.py --test-asc {asc} --dry-run")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
