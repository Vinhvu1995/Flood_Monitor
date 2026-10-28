"""Convert camera_locations.xml markers to GeoJSON.

Input:  camera_locations.xml with <marker id text color location="lat,lon">
Output: markers.geojson (WGS84 Points, id/text/color properties).

Usage: python xml_to_geojson.py camera_locations.xml [--out markers.geojson]
"""

from __future__ import annotations

import argparse
import json
import xml.etree.ElementTree as ET
from pathlib import Path


def convert(xml_path: str | Path) -> dict:
    root = ET.parse(xml_path).getroot()
    feats = []
    for m in root.iter("marker"):
        loc = (m.get("location") or "").split(",")
        if len(loc) != 2:
            continue
        try:
            lat, lon = float(loc[0]), float(loc[1])
        except ValueError:
            continue
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {"id": m.get("id"), "text": m.get("text"),
                           "color": m.get("color")},
        })
    return {"type": "FeatureCollection", "features": feats}


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("xml", help="camera_locations.xml")
    p.add_argument("--out", default="markers.geojson")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    fc = convert(args.xml)
    Path(args.out).write_text(json.dumps(fc, indent=1))
    print(f"wrote {len(fc['features'])} markers -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
