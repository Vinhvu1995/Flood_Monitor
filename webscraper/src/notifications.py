"""Local notifications (Teams removed).

All alert outputs are now local log records. No webhook, no network.
Kept pure builders so pipeline/validation code paths stay unchanged.
"""
import json, os, time

LOG_PATH = os.path.join("cron_logs", "notifications.log")

def _log(kind: str, title: str, text: str, facts=None):
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    rec = {"ts": int(time.time()), "kind": kind, "title": title,
           "text": (text or "")[:2000], "facts": facts or []}
    with open(LOG_PATH, "a") as f:
        f.write(json.dumps(rec) + "\n")
    return True

def post_to_teams(webhook: str = "", title: str = "", text: str = "",
                  facts=None, mode: str = "local"):
    """Compatibility shim: previously posted to Teams, now logs locally."""
    return _log("info", title, text, facts)

def build_card(kind: str, locations=None, text: str = "") -> dict:
    locs = list(locations or [])[:8]
    more = f" +{len(locations) - 8} more" if len(locations or []) > 8 else ""
    return {"kind": kind, "title": kind,
            "text": text or "; ".join(locs) + more,
            "facts": [{"name": "locations",
                       "value": "; ".join(locs) + more}]}

def validated_card(locations, groups, window, s3_uri=""):
    locs = list(locations or [])[:8]
    more = f" +{len(locations) - 8} more" if len(locations or []) > 8 else ""
    return {"title": "Flooding confirmed — validated (local log)",
            "text": "; ".join(locs) + more,
            "groups": groups or [], "window": window, "s3": s3_uri}
