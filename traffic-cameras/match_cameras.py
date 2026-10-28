"""Match XML markers to OCR camera descriptions (two-pass token filter).

For each marker's ``text``, progressive token filtering over the OCR
descriptions from camera_metadata_complete.csv:

- pass 1: only still-unused camera ids may match;
- pass 2: duplicates allowed (match_status "duplicate").

Adds matched_camera_id, full_description, image_url, view_url,
match_status -> merged_cameras_smart.geojson; also writes
unused_metadata_entries.csv (plus duplicate_matches_audit.csv).

Usage: python match_cameras.py markers.geojson camera_metadata_complete.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path

STOPWORDS = {"THE", "AT", "ON", "OF", "AND", "A", "TO", "CAMERA", "CAM"}


def tokens(text: str) -> list[str]:
    toks = [t for t in re.split(r"[^A-Za-z0-9]+", (text or "").upper()) if t]
    return [t for t in toks if t not in STOPWORDS]


def score(marker_toks: list[str], desc_toks: set[str]) -> float:
    """Fraction of marker tokens present in the description."""
    if not marker_toks:
        return 0.0
    return sum(1 for t in marker_toks if t in desc_toks) / len(marker_toks)


def best_match(marker_toks: list[str], candidates: list[dict]) -> dict | None:
    """Progressive token filter: keep candidates containing all tokens,
    relaxing one token at a time; tie-break by score then id."""
    if not marker_toks:
        return None
    cands = [(c, score(marker_toks, c["_toks"])) for c in candidates]
    cands = [c for c in cands if c[1] > 0]
    if not cands:
        return None
    for width in range(len(marker_toks), 0, -1):
        pool = [c for c in cands
                if sum(1 for t in marker_toks[:width] if t in c[0]["_toks"])
                == width]
        if pool:
            pool.sort(key=lambda c: (-c[1], str(c[0]["id"])))
            return pool[0][0]
    cands.sort(key=lambda c: (-c[1], str(c[0]["id"])))
    return cands[0][0]


def match(markers: list[dict], metas: list[dict]):
    """Two passes. Returns (merged_features, unused_metas, duplicates)."""
    for m in metas:
        m["_toks"] = set(tokens(m.get("desc", "")))
    used: set[str] = set()
    merged = []
    duplicates = []
    for pass_no in (1, 2):
        for feat in markers:
            if feat.get("_done"):
                continue
            mtoks = tokens(feat["text"])
            pool = [m for m in metas
                    if pass_no == 2 or str(m["id"]) not in used]
            hit = best_match(mtoks, pool)
            if hit is None or best_match(mtoks, [hit]) is None:
                continue
            if score(mtoks, hit["_toks"]) < 0.5:
                continue
            feat["_done"] = True
            status = "matched" if str(hit["id"]) not in used else "duplicate"
            used.add(str(hit["id"]))
            if status == "duplicate":
                duplicates.append((feat["text"], hit["id"]))
            props = dict(feat.get("properties", {}))
            props.update({
                "matched_camera_id": hit["id"],
                "full_description": hit["desc"],
                "image_url": hit.get("url", ""),
                "view_url": hit.get("view_url", ""),
                "match_status": status,
            })
            merged.append({"type": "Feature", "geometry": feat["geometry"],
                           "properties": props})
    unmatched = [f for f in markers if not f.get("_done")]
    for f in unmatched:
        props = dict(f.get("properties", {}))
        props.update({"matched_camera_id": None, "full_description": "",
                      "image_url": "", "view_url": "",
                      "match_status": "unmatched"})
        merged.append({"type": "Feature", "geometry": f["geometry"],
                       "properties": props})
    unused = [m for m in metas if str(m["id"]) not in used]
    return merged, unused, duplicates


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("markers", help="markers.geojson")
    p.add_argument("metadata", help="camera_metadata_complete.csv")
    p.add_argument("--out", default="merged_cameras_smart.geojson")
    p.add_argument("--unused-out", default="unused_metadata_entries.csv")
    p.add_argument("--dup-out", default="duplicate_matches_audit.csv")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    with open(args.markers) as fh:
        markers = [{"geometry": f["geometry"], "text": f["properties"].get("text", ""),
                    "properties": f["properties"]}
                   for f in json.load(fh)["features"]]
    with open(args.metadata, newline="") as fh:
        metas = [{"id": r["Camera_ID"], "desc": r.get("Description_Text", ""),
                  "url": r.get("Image_URL", ""),
                  "view_url": r.get("HTML_URL", "")}
                 for r in csv.DictReader(fh)]
    merged, unused, duplicates = match(markers, metas)
    Path(args.out).write_text(
        json.dumps({"type": "FeatureCollection", "features": merged}, indent=1))
    with open(args.unused_out, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["Camera_ID", "Description_Text"])
        for m in unused:
            w.writerow([m["id"], m["desc"]])
    with open(args.dup_out, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["marker_text", "Camera_ID"])
        w.writerows(duplicates)
    n_match = sum(1 for f in merged
                  if f["properties"]["match_status"] == "matched")
    print(f"matched={n_match} duplicates={len(duplicates)} "
          f"unmatched={len(merged) - n_match - len(duplicates)} "
          f"unused_meta={len(unused)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
