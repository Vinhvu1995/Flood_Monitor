"""NWS Local Storm Report parser + IEM poll (src/lsr_parser.py)."""
import re
from collections import namedtuple
from datetime import datetime
import urllib.request, json

LSR = namedtuple("LSR", ["time", "lat", "lon", "event", "county", "state",
                         "remark", "place_override"])

FLOOD_EVENTS = ("FLOOD", "FLASH FLOOD", "FLOODING", "WATER")
TIME_RE = re.compile(r"^\d{3,4}\s*(AM|PM)\b", re.I | re.M)

def is_lsr_page(url: str, text: str) -> bool:
    u = (url or "").lower()
    if "partnerservices.nws.noaa.gov/products/cache" in u and "lsr" in u:
        return True
    return "Local Storm Report" in (text or "") and "..TIME.." in (text or "")

def parse_lsr(text: str):
    """Split on timestamps, keep flood event types or flood words in remark."""
    recs = []
    chunks = re.split(r"(?m)^(?=\d{3,4}\s*(?:AM|PM))", text or "")
    for ch in chunks:
        if not TIME_RE.search(ch):
            continue
        lat = re.search(r"LAT\s*(-?[34]\d\.\d+)|([34]\d\.\d+)\s*[Nn]", ch)
        lon = re.search(r"LON\s*(-?\d{2,3}\.\d+)|(\d{2,3}\.\d+)\s*[Ww]", ch)
        if not (lat and lon):
            continue
        lat_v = next((g for g in lat.groups() if g), None)
        lon_v = next((g for g in lon.groups() if g), None)
        evt = (re.search(r"(FLASH FLOOD|FLOOD[A-Z ]*)", ch, re.I) or [None, ""])[1]
        remark = ch[:800]
        if not (evt or re.search(r"flood|inundat|submerg|washed away", ch, re.I)):
            continue
        county = (re.search(r"(?:COUNTY|PARISH)\s*[:\-]?\s*([A-Za-z .']+)", ch, re.I)
                  or [None, ""])[1].strip()
        road = re.search(r"(I-?\d+|US\s*\d+|Route\s*\d+|[\w ]+ (?:St|Road|Ave))", ch)
        lon_f = abs(float(lon_v)) if lon_v.lstrip().startswith("-") or "W" in ch.upper() else float(lon_v)
        recs.append(LSR(time="", lat=float(lat_v), lon=-lon_f,
                        event=(evt or "FLOOD").upper(), county=county, state="",
                        remark=remark,
                        place_override=road.group(1).strip() if road else ""))
    return recs

def fetch_iem_lsr_records(states, start: str, end: str):
    """Poll mesonet IEM LSR geojson. Never raises in pipeline (caller records)."""
    sts = f"{start}T00:00"
    ets = f"{end}T23:59"
    url = ("https://mesonet.agron.iastate.edu/geojson/lsr.geojson"
           f"?states={','.join(states)}&sts={sts}&ets={ets}")
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
        feats = data.get("features", [])
        return {"checked": True, "states": list(states),
                "points": len(feats), "failed": None}
    except Exception as e:
        return {"checked": True, "states": list(states),
                "points": 0, "failed": str(e)[:200]}
