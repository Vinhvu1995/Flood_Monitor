#!/usr/bin/env python3
"""publish_validated.py <run_dir> [--dry-run] [--allow-override]. Exits 3/4 on gate fail."""
import glob
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from validation_receipt import check_receipt  # noqa: E402


def main():
    run_dir, dry = sys.argv[1], "--dry-run" in sys.argv
    allow = "--allow-override" in sys.argv
    vfiles = glob.glob(os.path.join(run_dir, "Vectorised_Files", "*.geojson"))
    if not vfiles:
        print("no vector files; nothing to publish")
        return 0
    ok, msg = check_receipt(run_dir, allow)
    if not ok:
        print(f"receipt check failed: {msg}", file=sys.stderr)
        return 3
    sums = sorted(glob.glob(os.path.join(run_dir, "Logs", "*_run_summary.json")))
    s3 = ""
    if sums:
        with open(sums[-1]) as f:
            s3 = json.load(f).get("s3_uri", "")
    if not s3.startswith("s3://"):
        print("no s3:// s3_uri in run summary", file=sys.stderr)
        return 4
    if dry:
        print(f"would upload {len(vfiles)} files + receipt to {s3}")
        return 0
    with open(os.path.join(run_dir, "Logs", "publish_receipt.json"), "w") as f:
        json.dump({"s3_uri": s3, "files": len(vfiles)}, f, indent=2)
    print(f"published {len(vfiles)} files to {s3}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
