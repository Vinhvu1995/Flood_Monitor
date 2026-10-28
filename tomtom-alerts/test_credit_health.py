"""Tests for credit_health (Teams removed — local log). Run: python test_credit_health.py."""
import io, json, sys, tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import credit_health as ch

KEY = "A" * 32
NO_CREDITS_BODY = '{"detailedError":{"code":"InsufficientFunds","message":"You do not have enough credits"}}'
T0 = datetime(2026, 10, 26, 9, 0, tzinfo=UTC)

class Resp:
    def __init__(self, code, text=""):
        self.status_code, self.text = code, text

def run(responses, tmp, start=T0, step_hours=0.5):
    real_get = ch.requests.get
    try:
        for i, r in enumerate(responses):
            ch.requests.get = lambda *a, _r=r, **k: _r
            ch.check(KEY, state_dir=tmp, now=start + timedelta(hours=step_hours * i))
    finally:
        ch.requests.get = real_get

def logged(tmp):
    p = Path(tmp) / "credit_health.log"
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text().splitlines()]

def test_classify():
    assert ch.classify(200) == "OK"
    assert ch.classify(403, NO_CREDITS_BODY) == "NO_CREDITS"
    assert ch.classify(403, "forbidden") == "DENIED"
    assert ch.classify(401, "") == "DENIED"
    assert ch.classify(429, "") == "RATE_LIMITED"
    assert ch.classify(500, "") == "ERROR" and ch.classify(0, "") == "ERROR"

def test_redact_hides_keys_in_urls_and_streams():
    url = f"403 Client Error: Forbidden for url: https://api.tomtom.com/x.pbf?key={KEY}&bbox=1"
    assert KEY not in ch.redact(url) and "key=<redacted>&bbox=1" in ch.redact(url)
    buf = io.StringIO()
    s = ch._RedactingStream(buf)
    s.write(url)
    assert KEY not in buf.getvalue()

def test_one_alert_on_exhaustion_then_silence_then_daily_reminder_then_recovery():
    tmp = tempfile.mkdtemp()
    bad = Resp(403, NO_CREDITS_BODY)
    run([Resp(200)] + [bad] * 49, tmp)
    kinds = [(e["kind"], e["status"]) for e in logged(tmp)]
    assert kinds[0] == ("alert", "NO_CREDITS"), kinds
    assert kinds[1] == ("remind", "NO_CREDITS"), kinds
    assert len(kinds) == 2, kinds
    run([Resp(200)], tmp, start=T0 + timedelta(hours=40))
    assert logged(tmp)[-1]["kind"] == "recovered"
    status = json.loads((Path(tmp) / "credit_health_status.json").read_text())
    assert status["status"] == "OK" and status["since"] is None and status["last_ok"]

def test_healthy_runs_never_log():
    tmp = tempfile.mkdtemp()
    run([Resp(200)] * 5, tmp)
    assert logged(tmp) == []

def test_status_change_logs_again():
    tmp = tempfile.mkdtemp()
    run([Resp(200), Resp(403, NO_CREDITS_BODY), Resp(429, "")], tmp)
    assert [(e["kind"], e["status"]) for e in logged(tmp)] == [
        ("alert", "NO_CREDITS"), ("alert", "RATE_LIMITED")]

def test_network_failure_is_logged_and_never_raises():
    tmp = tempfile.mkdtemp()
    real = ch.requests.get
    def boom(*a, **k):
        raise ConnectionError(f"https://x?key={KEY}")
    ch.requests.get = boom
    try:
        out = ch.check(KEY, state_dir=tmp, now=T0)
    finally:
        ch.requests.get = real
    assert out["status"] == "ERROR" and KEY not in json.dumps(out)

def main() -> int:
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn(); print("PASS", name)
            except Exception as e:
                fails += 1; print("FAIL", name, repr(e)[:300])
    print(f"\n{'all passed' if not fails else str(fails) + ' failed'}")
    return 1 if fails else 0

if __name__ == "__main__":
    raise SystemExit(main())
