"""Rainfall-triggered camera downloader (8mm default, 6h window, 15min)."""
import argparse, hashlib, json, os, time
from datetime import datetime, timezone

PLACEHOLDER_SHA = "8708706daa9dc2aea3978bd7ee79771708a38eb17eb7b341f78993225b99160fd"
PLACEHOLDER_SIZE = 21422
THRESHOLD = 8.0
WINDOW_HOURS = 6
INTERVAL_MIN = 15
STATE_FILE = os.environ.get("FLOODWATCH_STATE",
                            "/nfs/develop_vinh/tmp/floodwatch_state.json")

def asc_path(rainfall_dir, when_ts=None):
    dt = datetime.fromtimestamp(when_ts or time.time(), tz=timezone.utc)
    n = (dt.hour - 2) % 24 + 1
    day = dt.strftime("%Y%m%d")
    if dt.hour < 2:
        from datetime import timedelta as _td
        day = (dt - _td(days=1)).strftime("%Y%m%d")
    return os.path.join(rainfall_dir, day, f"rainfall{n}.asc")

def is_placeholder(data: bytes) -> bool:
    return len(data) == PLACEHOLDER_SIZE and \
        hashlib.sha256(data).hexdigest() == PLACEHOLDER_SHA

def is_dark(data: bytes) -> bool:
    # brightness std < 20 -> faulty/dark (needs PIL at runtime; fallback: tiny files)
    if len(data) < 5000:
        return True
    try:
        from PIL import Image
        import io
        im = Image.open(io.BytesIO(data)).convert("L").resize((64, 64))
        px = list(im.getdata())
        mean = sum(px) / len(px)
        var = sum((p - mean) ** 2 for p in px) / len(px)
        return var ** 0.5 < 20
    except Exception:
        return False

def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except FileNotFoundError:
        return {"windows": {}}

def classify_downloaded(image_bytes: bytes, sidecar_path: str | None = None) -> dict:
    """Post-download hook: Spark vision + SAM 3.1 when enabled, else heuristic."""
    import sys as _sys
    _sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from vision import classify_flood
    return classify_flood(image_bytes, sidecar_path)

def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--threshold", type=float, default=THRESHOLD)
    p.add_argument("--window-hours", type=float, default=WINDOW_HOURS)
    p.add_argument("--rainfall-dir", default="/nfs/develop_pff/processing/UK/national/Rainfall/Hourly")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force", action="store_true")
    p.add_argument("--test-rainfall", type=float, default=None)
    p.add_argument("--test-asc", default=None)
    a = p.parse_args(argv)
    state = load_state()
    print(json.dumps({"asc": asc_path(a.rainfall_dir), "threshold": a.threshold,
                      "windows": len(state.get("windows", {}))}))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
