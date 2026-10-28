"""Radar palette + ASC cells + bbox helpers + credit health (Teams removed)."""
import math, os, json, time, urllib.request

# radar_palette: HIGH rain = HOT or EXTREME only
def classify_pixel(r, g, b, a):
    if a == 0:
        return "transparent"
    if a != 255:
        return "snow"
    if (r, g, b) == (255, 255, 255):
        return "white"
    if b == 255 and r == 255:
        return "EXTREME"
    if b == 0:
        return "HOT"
    if 0 < b < 255:
        return "BLUE"
    return "UNKNOWN"

ASC_THRESHOLD_MM = 8.0

def pad_floor_area(bbox_km2, side_km, pad_km=8, floor_km=15):
    return max(bbox_km2, floor_km * floor_km)

def uk_land_test(lon, lat):
    return 49.9 < lat < 60.9 and -8.2 < lon < 1.8

# credit_health: SW London probe
PROBE_BBOX = (-0.30, 51.45, -0.25, 51.50)

def check_credit(api_key):
    url = ("https://api.tomtom.com/traffic/services/5/incidentDetails"
           f"?key={api_key}&bbox={PROBE_BBOX[0]},{PROBE_BBOX[1]},"
           f"{PROBE_BBOX[2]},{PROBE_BBOX[3]}"
           "&fields=" + urllib.parse.quote("{incidents{type}}", safe="")) \
        if False else "probe"
    import urllib.parse
    url = ("https://api.tomtom.com/traffic/services/5/incidentDetails"
           f"?key={api_key}&bbox=-0.30,51.45,-0.25,51.50"
           "&fields=" + urllib.parse.quote("{incidents{type}}", safe=""))
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            return "OK", r.status
    except Exception as e:
        s = str(e)
        if "429" in s:
            return "RATE_LIMITED", 429
        if "402" in s or "InsufficientFunds" in s or "enough credits" in s:
            return "NO_CREDITS", 402
        if any(c in s for c in ("401", "403")):
            return "DENIED", 403
        return "ERROR", 0

ALERTED = "state/alerted_incidents.json"

def should_alert(incident_id, now=None):
    now = now or time.time()
    try:
        with open(ALERTED) as f:
            seen = json.load(f)
    except FileNotFoundError:
        seen = {}
    # prune after 3 days
    seen = {k: v for k, v in seen.items() if now - v < 3 * 86400}
    if incident_id in seen:
        return False, seen
    seen[incident_id] = now
    os.makedirs(os.path.dirname(ALERTED), exist_ok=True)
    with open(ALERTED, "w") as f:
        json.dump(seen, f)
    return True, seen
