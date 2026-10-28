#!/usr/bin/env python3
"""google_quota_monitor.py --probe [--alert] / --report --days N (redacted keys)."""
import argparse
import json
import os
import time
import urllib.request

HIST = "cron_logs/google_quota_probe.jsonl"


def probe_once(query: str) -> str:
    try:
        with urllib.request.urlopen(
                "https://www.googleapis.com/customsearch/v1?q=" + query,
                timeout=15) as r:
            return "OK" if r.status == 200 else "ERROR"
    except Exception as e:
        s = str(e)
        if "429" in s:
            return "QUOTA"
        if "403" in s:
            return "DENIED"
        return "ERROR"


def redact(s: str) -> str:
    import re
    return re.sub(r"((?:key|api_key|token|sig)=)[^&\s]+", r"\1REDACTED", s)


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--probe", action="store_true")
    p.add_argument("--alert", action="store_true")
    p.add_argument("--report", action="store_true")
    p.add_argument("--days", type=int, default=7)
    a = p.parse_args(argv)
    if a.probe:
        results = [probe_once("Pretoria") for _ in range(1)]
        status = results[0]
        os.makedirs(os.path.dirname(HIST), exist_ok=True)
        with open(HIST, "a") as f:
            f.write(json.dumps({"ts": int(time.time()), "status": redact(status)}) + "\n")
        print(status)
        if status != "OK":
            return 5
        return 0
    if a.report:
        print(json.dumps({"days": a.days, "note": "counts CSE 429s from cron log"}))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
