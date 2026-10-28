"""credit_health.py - detect TomTom credit exhaustion, log it locally (Teams removed).

The monitor swallows HTTP 403 from TomTom, so an account with no credits looked like
"no flooding" (2026-09 to 2026-10: empty parquets for weeks). Each run calls check():

  1. one small Incident Details request (about 1 of the ~17 calls a run already makes);
  2. the result is appended to state/credit_health.jsonl and the latest status is
     written to state/credit_health_status.json (monitor either file);
  3. Teams is alerted when the status leaves OK, again every REMIND_HOURS while it stays
     bad, and once when it recovers.

Statuses: OK, NO_CREDITS (403 InsufficientFunds), DENIED (other 401/403), RATE_LIMITED
(429), ERROR (network or 5xx). check() never raises: it must not stop a monitor run.
Keys are never logged: redact() strips key=... and install_log_redaction() applies it to
everything the monitor prints.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import UTC, datetime, timedelta

import requests

STATE_DIR = os.environ.get("STATE_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "state"))
HISTORY_PATH = os.path.join(STATE_DIR, "credit_health.jsonl")
STATUS_PATH = os.path.join(STATE_DIR, "credit_health_status.json")
REMIND_HOURS = 24
PROBE_URL = "https://api.tomtom.com/traffic/services/5/incidentDetails"
PROBE_BBOX = "-0.30,51.45,-0.25,51.50"  # a few km in south-west London

_KEY_RE = re.compile(r"(key=)[A-Za-z0-9_\-]{16,}", re.IGNORECASE)


def redact(text: object) -> str:
    return _KEY_RE.sub(r"\1<redacted>", str(text))


class _RedactingStream:
    def __init__(self, stream):
        self._stream = stream

    def write(self, s):
        return self._stream.write(redact(s) if isinstance(s, str) else s)

    def __getattr__(self, name):
        return getattr(self._stream, name)


def install_log_redaction() -> None:
    """Redact API keys from stdout and stderr (the 403 warnings print the full URL)."""
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name)
        if not isinstance(stream, _RedactingStream):
            setattr(sys, name, _RedactingStream(stream))


def classify(status_code: int, body: str = "") -> str:
    if status_code == 200:
        return "OK"
    if status_code == 0:
        return "ERROR"
    low = (body or "").lower()
    if status_code == 429:
        return "RATE_LIMITED"
    if status_code in (401, 402, 403):
        if "insufficientfunds" in low or "enough credits" in low or status_code == 402:
            return "NO_CREDITS"
        return "DENIED"
    return "ERROR"


def probe(api_key: str, now: datetime | None = None, timeout: int = 15) -> dict:
    now = now or datetime.now(UTC)
    result = {"time": now.isoformat(timespec="seconds")}
    try:
        r = requests.get(
            PROBE_URL,
            params={"key": api_key, "bbox": PROBE_BBOX, "fields": "{incidents{type}}", "language": "en-GB"},
            timeout=timeout,
        )
        result["http"] = r.status_code
        result["status"] = classify(r.status_code, r.text)
        if result["status"] != "OK":
            result["detail"] = redact(r.text)[:200]
    except Exception as exc:  # noqa: BLE001 - network failure: say so, never print the URL
        result.update(http=0, status="ERROR", detail=type(exc).__name__)
    return result


def _read_status(path: str) -> dict:
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def decide(prev: dict, result: dict, now: datetime) -> str | None:
    """"alert", "remind", "recovered" or None. prev is the saved status file."""
    cur, before = result["status"], prev.get("status")
    if cur == "OK":
        return "recovered" if before not in (None, "OK") else None
    if before in (None, "OK") or before != cur:
        return "alert"
    last = prev.get("last_alert")
    try:
        due = datetime.fromisoformat(last) + timedelta(hours=REMIND_HOURS) <= now
    except (TypeError, ValueError):
        due = True
    return "remind" if due else None


_MEANING = {
    "NO_CREDITS": "The TomTom account has no credits (HTTP 403 InsufficientFunds). The monitor is "
    "collecting no incidents, so empty runs do NOT mean no flooding. Top up the account on the TomTom portal.",
    "DENIED": "TomTom refused the key (HTTP 401/403, not a credit error). Check the key and its API access.",
    "RATE_LIMITED": "TomTom is rate limiting the key (HTTP 429).",
    "ERROR": "The TomTom probe failed (network error or server error).",
}


def _card(title: str, kind: str, result: dict, since: str | None) -> dict:
    """Adaptive card in the same shape as the flood monitor alerts (FactSet under a title)."""
    facts = [{"title": "Status", "value": "OK" if kind == "recovered" else result["status"]}]
    if kind != "recovered":
        facts.append({"title": "Meaning", "value": _MEANING.get(result["status"], "")})
        facts.append({"title": "HTTP", "value": str(result.get("http"))})
    if since:
        facts.append({"title": "Failing since" if kind != "recovered" else "Was failing since", "value": f"{since} UTC"})
    return {
        "contentType": "application/vnd.microsoft.card.adaptive",
        "content": {
            "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
            "type": "AdaptiveCard",
            "version": "1.5",
            "body": [
                {"type": "TextBlock", "weight": "Bolder", "size": "Medium", "text": title},
                {"type": "TextBlock", "isSubtle": True, "wrap": True, "text": f"{result['time']} UTC"},
                {"type": "FactSet", "facts": facts},
            ],
        },
    }


def _send(kind: str, result: dict, since: str | None, state_dir: str | None = None) -> bool:
    """Teams removed: append to local credit_health log. Always 'delivered'."""
    try:
        base = state_dir or os.path.dirname(STATUS_PATH)
        log_path = os.path.join(base, "credit_health.log")
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "a") as f:
            f.write(json.dumps({"kind": kind, "status": result.get("status"),
                                "http": result.get("http"), "time": result.get("time"),
                                "since": since}) + "\n")
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"  [credit-health] local log failed: {type(exc).__name__}")
        return False


def check(api_key: str, state_dir: str | None = None, now: datetime | None = None) -> dict:
    """Probe, log, alert. Returns the probe result. Never raises."""
    try:
        d = state_dir or STATE_DIR
        history, status_path = os.path.join(d, "credit_health.jsonl"), os.path.join(d, "credit_health_status.json")
        now = now or datetime.now(UTC)
        result = probe(api_key, now)
        prev = _read_status(status_path)
        kind = decide(prev, result, now)
        since = prev.get("since") if prev.get("status") == result["status"] else None
        if result["status"] != "OK" and since is None:
            since = result["time"]
        delivered = _send(kind, result, prev.get("since") if kind == "recovered" else since, d) if kind else False
        os.makedirs(d, exist_ok=True)
        entry = dict(result, alerted=kind if delivered else None)
        with open(history, "a") as f:
            f.write(json.dumps(entry) + "\n")
        status = {
            "status": result["status"],
            "http": result.get("http"),
            "checked": result["time"],
            "since": since if result["status"] != "OK" else None,
            "last_alert": result["time"] if delivered and kind != "recovered" else prev.get("last_alert"),
        }
        if result["status"] == "OK":
            status["last_ok"] = result["time"]
        else:
            status["last_ok"] = prev.get("last_ok")
        if not delivered and kind in ("alert", "remind"):
            status["last_alert"] = prev.get("last_alert")  # not delivered: try again next run
            if kind == "alert":
                status["status"], status["since"] = prev.get("status") or "OK", prev.get("since")
                status["pending_status"] = result["status"]
        tmp = status_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(status, f)
        os.replace(tmp, status_path)
        print(f"  [credit-health] {result['status']} (http {result.get('http')})")
        return result
    except Exception as exc:  # noqa: BLE001
        print(f"  [credit-health] check failed: {type(exc).__name__}")
        return {"status": "ERROR", "detail": type(exc).__name__}
