#!/usr/bin/env python3
"""send_teams_card.py — Teams removed. Logs locally, keeps receipt gate."""
import argparse, os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from notifications import _log
from validation_receipt import check_receipt

def log_send(kind, country=""):
    os.makedirs("cron_logs", exist_ok=True)
    with open("cron_logs/card_sends.log", "a") as f:
        f.write(f"{int(time.time())}\t{kind}\t{country}\n")

def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("kind")
    p.add_argument("--country", default="")
    p.add_argument("--window-start", default="")
    p.add_argument("--window-end", default="")
    p.add_argument("--evidence", default="")
    p.add_argument("--locations", default="")
    p.add_argument("--run-dir", default="")
    p.add_argument("--reason", default="")
    p.add_argument("--details", default="")
    a = p.parse_args(argv)
    if a.kind == "validated" and a.run_dir:
        ok, reason = check_receipt(a.run_dir)
        if not ok:
            print(f"validated log refused: {reason}")
            return 3
    title = {"no-flood": "No flooding detected",
             "uncertain": "Possible flooding — needs human review",
             "detected": "Flooding detected",
             "validated": "Flooding confirmed — validated (local)",
             "monitor-alert": "Daily flood monitor needs attention"}.get(a.kind, a.kind)
    text = a.evidence or a.locations or a.reason or a.kind
    _log(a.kind, title, text, [("country", a.country),
                              ("window", f"{a.window_start}..{a.window_end}")])
    print(f"(local log) {title}: {text[:200]}")
    log_send(a.kind, a.country)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
