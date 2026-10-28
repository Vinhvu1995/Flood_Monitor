"""Tiered local geocoder adapter (offline stub of local_geocoder.py).

Tier order mirrors the spec: postcode > road > building > river/waterway > area >
Google point (coarsened) > master-list fallback. Without datasets it returns
lightweight point features with confidence + location_type so the pipeline runs.
"""
import re

ROAD_SUFFIX = re.compile(r"\b(road|rd|street|st|avenue|lane|drive|close|crescent|"
                         r"boulevard|terrace|mews|grove|parade|broadway|embankment|"
                         r"highway|expressway|freeway|parkway|turnpike|motorway|bypass)\b", re.I)
WATER_WORD = re.compile(r"\b(river|stream|canal|brook|beck|burn|weir|ditch|drain|estuary)\b", re.I)
US_ROUTE = re.compile(r"\b([A-Z]-?\d+|US\s*\d+|[A-Z]{2}\s*\d+)\b")
UK_POSTCODE = re.compile(r"\b[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}\b", re.I)


def infer_location_type(first_part: str) -> str:
    p = (first_part or "").strip()
    if UK_POSTCODE.search(p):
        return "postcode"
    if US_ROUTE.search(p):
        return "road"
    if re.match(r"^\d+\s", p):
        return "building"
    if ROAD_SUFFIX.search(p):
        return "road"
    if WATER_WORD.search(p):
        return "river"
    return "area"


def geoencode_local(rows: list[dict], target_epsg: int = 27700, region: str = "UK") -> dict:
    """rows: [{"name": ...}]. Returns {polygons, lines, points, failed} (stub points)."""
    points, failed = [], []
    seen = set()
    for r in rows:
        name = (r.get("name") or "").strip()
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        first = name.split(",")[0]
        ltype = infer_location_type(first)
        if region == "US" and ltype == "area" and len(name.split()) <= 3 and "," not in name:
            failed.append({"name": name, "reason": "vague_region"})
            continue
        points.append({"name": name, "location_type": ltype, "confidence": 0.5,
                       "geometry_kind": "point", "epsg": target_epsg})
    return {"polygons": [], "lines": [],
            "points": points, "failed": failed}
