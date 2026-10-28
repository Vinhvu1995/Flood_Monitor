"""TomTom monitor: credit check, Stage A details (cat 11), Stage B vectors, Stage C rain."""
import json, os, time, urllib.request, urllib.parse

DETAILS = ("https://api.tomtom.com/traffic/services/5/incidentDetails"
           "?key={key}&bbox={bbox}&language=en-GB&fields={fields}")
FIELDS = ("{incidents{type,geometry{type,coordinates},properties{id,iconCategory,"
          "magnitudeOfDelay,length,events{description,code,iconCategory},"
          "from,to,roadNumbers,startTime,endTime}}}")
VECTOR_BUDGET = 3000
CATEGORY_NAMES = {0: "Unknown", 1: "Accident", 2: "Fog", 3: "Dangerous Conditions",
                  4: "Rain", 5: "Ice", 6: "Jam", 7: "Lane Closed", 8: "Road Closed",
                  9: "Road Works", 10: "Wind", 11: "Flooding", 12: "Detour",
                  13: "Cluster", 14: "Broken Down Vehicle"}

def is_flood(props):
    if props.get("iconCategory") == 11:
        return True
    return any(e.get("iconCategory") == 11 for e in props.get("events", []))

def fetch_details(api_key, bbox):
    q = DETAILS.format(key=api_key,
                       bbox=",".join(map(str, bbox)),
                       fields=urllib.parse.quote(FIELDS, safe=""))
    last = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(q, timeout=20) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except Exception as e:
            last = str(e)
            if "429" in last:
                time.sleep(2 ** attempt)
            else:
                break
        time.sleep(0.1)
    raise RuntimeError(f"details failed: {last}")

def asc_hour_file(folder: str, when_ts: float) -> str:
    """rainfall1.asc = hour starting 02:00 UTC on folder date."""
    import datetime as _d
    dt = _d.datetime.utcfromtimestamp(when_ts)
    n = (dt.hour - 2) % 24 + 1
    if dt.hour < 2:
        dt = dt - _d.timedelta(days=1)
    return os.path.join(folder, dt.strftime("%Y%m%d"), f"rainfall{n}.asc")

def main():
    key = os.environ.get("TOMTOM_API_KEY", "")
    print(json.dumps({"vector_budget": VECTOR_BUDGET,
                      "flood_category": 11,
                      "asc_naming": "rainfall1.asc = 02:00 UTC hour"}))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
