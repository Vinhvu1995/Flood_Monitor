"""Local flood-detection log (Teams removed).

Same dedup contract as before: one log record per incident_id per
ALERT_MEMORY_DAYS. No webhook, no network. Failures never raise.
"""
import json, os, time
from datetime import datetime, timedelta, timezone

STATE_DIR = os.environ.get(
    "STATE_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "state")
)
ALERTED_PATH = os.path.join(STATE_DIR, "alerted_incidents.json")
LOCAL_LOG = os.path.join(STATE_DIR, "flood_alerts.log")
ALERT_MEMORY_DAYS = 3


def _load_alerted(path: str = ALERTED_PATH) -> dict:
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_alerted(alerted: dict, path: str = ALERTED_PATH) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(alerted, f, indent=2)
    os.replace(tmp, path)


def notify_flood_detected(row: dict) -> None:
    os.makedirs(os.path.dirname(LOCAL_LOG), exist_ok=True)
    with open(LOCAL_LOG, "a") as f:
        f.write(json.dumps({
            "ts": datetime.now(timezone.utc).isoformat(),
            "incident_id": row.get("incident_id"),
            "from": row.get("from_road"), "to": row.get("to_road"),
            "roads": row.get("road_numbers"),
            "rainfall_latest_mm": row.get("rainfall_latest_mm"),
            "rainfall_24h_mm": row.get("rainfall_24h_mm"),
        }) + "\n")


def send_flood_alerts(rows: list[dict], now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    alerted = _load_alerted()
    cutoff = now - timedelta(days=ALERT_MEMORY_DAYS)
    alerted = {iid: ts for iid, ts in alerted.items()
               if datetime.fromisoformat(ts) > cutoff}
    stats = {"sent": 0, "skipped_duplicate": 0, "failed": 0}
    for row in rows:
        incident_id = row.get("incident_id")
        if not incident_id:
            continue
        if incident_id in alerted:
            stats["skipped_duplicate"] += 1
            continue
        try:
            notify_flood_detected(row)
            alerted[incident_id] = now.isoformat()
            stats["sent"] += 1
        except Exception as e:
            print(f"  [alerts] local log failed for {incident_id}: {e}")
            stats["failed"] += 1
    _save_alerted(alerted)
    return stats
