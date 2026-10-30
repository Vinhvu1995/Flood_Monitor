"""Rainfall-triggered camera downloader (8mm default, 6h window, 15min).

Contract mirrors the Paris original (see handover 09):
- ASC naming: rainfall1.asc = hour starting 02:00 UTC on the folder date.
- Next-hour lookup gated on UTC minute<=10 unless --force/--dry-run.
- Placeholder frames (exact size + SHA-256) and dark frames
  (brightness std < 20) are discarded as faulty.
- State lives on NFS with /tmp fallback; TRAFFIC_CAM_STATE_FILE overrides.
- classify_downloaded() post-download hook: Spark vision + SAM 3.1 when
  enabled, else heuristic (never raises).
"""
import argparse, hashlib, json, os, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PLACEHOLDER_SHA256 = "8708706daa9dc2aea3978bd7ee79771708a38eb17eb7b341f78993225b99160fd"
PLACEHOLDER_SHA = PLACEHOLDER_SHA256  # legacy alias
PLACEHOLDER_SIZE = 21422
BRIGHTNESS_STD_MIN = 20.0
GEOJSON_FILE = "Camera_locations/Traffic_Cameras_Road_Flood_Risk.geojson"
RAINFALL_DIR = "/nfs/develop_pff/processing/UK/national/Rainfall/Hourly"
THRESHOLD = 8.0
WINDOW_HOURS = 6
INTERVAL_MIN = 15
STATE_FILE = os.environ.get(
    "FLOODWATCH_STATE", "/nfs/develop_vinh/tmp/floodwatch_state.json")
FALLBACK_STATE_FILE = "/tmp/floodwatch_state.json"


def asc_path_for_hour(when: datetime, rainfall_dir) -> Path:
    """Folder date + rainfallN for the hour containing `when` (naive UTC ok)."""
    n = (when.hour - 2) % 24 + 1
    day = when.strftime("%Y%m%d")
    if when.hour < 2:
        day = (when - timedelta(days=1)).strftime("%Y%m%d")
    return Path(rainfall_dir) / day / f"rainfall{n}.asc"


def asc_path(rainfall_dir, when_ts=None) -> str:
    when = (datetime.fromtimestamp(when_ts, tz=timezone.utc).replace(tzinfo=None)
            if when_ts is not None else datetime.utcnow())
    return str(asc_path_for_hour(when, rainfall_dir))


def lookup_allowed(now: datetime, args) -> bool:
    """UTC minute<=10 gate; --force and --dry-run bypass."""
    if getattr(args, "force", False) or getattr(args, "dry_run", False):
        return True
    return now.minute <= 10


def read_asc_value(path, x: float, y: float):
    """Grid value at (x, y); None on missing/nodata/out-of-bounds."""
    try:
        lines = Path(path).read_text().splitlines()
    except (OSError, ValueError):
        return None
    try:
        header = {}
        for line in lines[:6]:
            k, _, v = line.partition(" ")
            header[k.strip().lower()] = v.strip()
        ncols = int(header["ncols"])
        nrows = int(header["nrows"])
        xll = float(header["xllcorner"])
        yll = float(header["yllcorner"])
        cell = float(header["cellsize"])
        nodata = float(header.get("nodata_value", "-9999"))
        grid = [[float(v) for v in r.split()] for r in lines[6:6 + nrows]]
    except (ValueError, IndexError, KeyError):
        return None
    col = int((x - xll) / cell)
    row = (nrows - 1) - int((y - yll) / cell)
    if not (0 <= col < ncols and 0 <= row < nrows):
        return None
    try:
        v = grid[row][col]
    except IndexError:
        return None
    return None if v == nodata else v


def is_placeholder(data: bytes) -> bool:
    return len(data or b"") == PLACEHOLDER_SIZE and \
        hashlib.sha256(data).hexdigest() == PLACEHOLDER_SHA256


def brightness_std(image_bytes: bytes):
    """Std of grayscale pixels; None when undecodable."""
    try:
        from PIL import Image
        import io
        im = Image.open(io.BytesIO(image_bytes)).convert("L")
        px = list(im.getdata())
        if not px:
            return None
        mean = sum(px) / len(px)
        return (sum((p - mean) ** 2 for p in px) / len(px)) ** 0.5
    except Exception:
        return None


def _is_faulty_image(image_bytes: bytes) -> bool:
    if is_placeholder(image_bytes or b""):
        return True
    try:
        std = brightness_std(image_bytes)
    except Exception:
        return False
    if std is None:
        return False  # unreadable -> saved, never silently dropped
    return std < BRIGHTNESS_STD_MIN


def atomic_write(path, data) -> None:
    """Write bytes/str atomically (tmp + replace), creating parents."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + f".tmp-{os.getpid()}")
    mode = "w" if isinstance(data, str) else "wb"
    with open(tmp, mode) as f:
        f.write(data)
    os.replace(tmp, p)


def fetch_image(url: str, timeout: int = 30, max_bytes: int = 10_000_000):
    """Download one image; None on any failure (never raises)."""
    if not (url or "").startswith("http"):
        return None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            if "image" not in (r.headers.get("Content-Type", "") or "").lower():
                return None
            data = r.read(max_bytes + 1)
            return data if data and len(data) <= max_bytes else None
    except Exception:
        return None


def load_cameras(geojson_file: str = GEOJSON_FILE) -> list:
    """Cameras as [{id, e, n}] in the ASC grid CRS (OSGB when pyproj exists)."""
    with open(geojson_file) as f:
        fc = json.load(f)
    try:
        from pyproj import Transformer
        to_osgb = Transformer.from_crs("EPSG:4326", "EPSG:27700", always_xy=True).transform
    except Exception:
        to_osgb = None
    cams = []
    for feat in fc.get("features", []):
        try:
            lon, lat = feat["geometry"]["coordinates"][:2]
            pr = feat.get("properties", {})
            if to_osgb is not None:
                e, n = to_osgb(lon, lat)
            else:
                e, n = lon, lat
            cams.append({"id": str(pr.get("matched_camera_id") or pr.get("id") or ""),
                         "e": e, "n": n,
                         "desc": pr.get("full_description") or pr.get("text", "")})
        except (KeyError, TypeError, ValueError):
            continue
    return cams


def is_dark(data: bytes) -> bool:
    # legacy alias: faulty/dark frames (placeholder or low-std)
    try:
        return bool(_is_faulty_image(data))
    except Exception:
        return len(data or b"") < 5000


def resolve_state_path(explicit=None) -> Path:
    if explicit:
        return Path(explicit)
    env = os.environ.get("TRAFFIC_CAM_STATE_FILE")
    if env:
        return Path(env)
    primary = Path(STATE_FILE)
    try:
        if primary.parent.exists():
            return primary
    except OSError:
        pass
    return Path(FALLBACK_STATE_FILE)


def save_state(path, data: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w") as f:
        json.dump(data, f)


def load_state(path=None) -> dict:
    p = Path(path) if path is not None else resolve_state_path()
    try:
        with open(p) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {"windows": {}}
    except (FileNotFoundError, ValueError, OSError):
        return {"windows": {}}


def decide_triggered(cams, args, now: datetime):
    """(triggered_cams, asc_path_or_None). Gate-closed -> ([], None).

    An explicit test_rainfall injection bypasses the minute gate (it stands
    in for an already-read value); live lookups respect the gate.
    """
    threshold = getattr(args, "threshold", THRESHOLD)
    test_rain = getattr(args, "test_rainfall", None)
    if test_rain is None and not lookup_allowed(now, args):
        return [], None
    rainfall_dir = getattr(args, "rainfall_dir", "")
    nxt = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    asc = getattr(args, "test_asc", None) or str(asc_path_for_hour(nxt, rainfall_dir))
    out = []
    for cam in cams or []:
        value = test_rain if test_rain is not None else read_asc_value(asc, cam.get("e"), cam.get("n"))
        if value is not None and value >= threshold:
            out.append({**cam, "rainfall_mm": value})
    return out, asc


def classify_downloaded(image_bytes: bytes, sidecar_path: str | None = None) -> dict:
    """Post-download hook: Spark vision + SAM 3.1 when enabled, else heuristic."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
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
    if a.dry_run:
        try:
            from vision import enabled as _vision_on
            vision = "baseten" if _vision_on() else "heuristic"
        except Exception:
            vision = "heuristic"
        print(json.dumps({"dry_run": True, "asc": asc_path(a.rainfall_dir),
                          "threshold": a.threshold, "vision": vision,
                          "windows": len(state.get("windows", {}))}))
        return 0
    print(json.dumps({"asc": asc_path(a.rainfall_dir), "threshold": a.threshold,
                      "windows": len(state.get("windows", {}))}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
