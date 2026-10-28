#!/usr/bin/env python3
"""apply_validation.py <verdicts.json> <run_dir> [--dry-run] [--keep-uncovered]."""
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from validation_receipt import write_receipt  # noqa: E402

verdicts_path, run_dir = sys.argv[1], sys.argv[2]
dry = "--dry-run" in sys.argv
keep_uncovered = "--keep-uncovered" in sys.argv
with open(verdicts_path) as f:
    v = json.load(f)
verdicts = v.get("verdicts", v) if isinstance(v, dict) else v
if not verdicts:
    print("empty verdict list with records present: refusing (exit 2)", file=sys.stderr)
    sys.exit(2)
keep = {(d["url"], d["location"]) for d in verdicts if d.get("keep")}
files = [os.path.join(run_dir, "Vectorised_Files", fn) for fn in sorted(os.listdir(os.path.join(run_dir, "Vectorised_Files")))] if os.path.isdir(os.path.join(run_dir, "Vectorised_Files")) else []
raw, covered, dropped_exp, kept = 0, 0, 0, []
for path in files:
    if not path.endswith(".geojson"):
        continue
    with open(path) as f:
        gj = json.load(f)
    for ft in gj.get("features", []):
        raw += 1
        key = (ft["properties"].get("url"), ft["properties"].get("name"))
        verdict = next((d for d in verdicts if (d["url"], d["location"]) == key), None)
        if verdict is None:
            if keep_uncovered:
                kept.append(ft)
            continue
        covered += 1
        if verdict.get("keep"):
            kept.append(ft)
        else:
            dropped_exp += 1
    if not dry:
        bak = os.path.join(run_dir, "Vectorised_Files", "_pre_validation_backup")
        os.makedirs(bak, exist_ok=True)
        if not os.path.exists(os.path.join(bak, os.path.basename(path))):
            shutil.copy(path, os.path.join(bak, os.path.basename(path)))
        with open(path, "w") as f:
            json.dump({"type": "FeatureCollection", "features": kept}, f)
print(json.dumps({"raw": raw, "covered": covered, "dropped_explicit": dropped_exp, "kept": len(kept)}))
if not dry:
    write_receipt(run_dir, verdicts_path, verdicts, raw, covered, dropped_exp,
                  raw - covered, len(kept), keep_uncovered)
