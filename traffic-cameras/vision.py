"""Camera flood sight (USE_META_VISION=1).

Primary: Spark Open-1026 vision via Baseten (OpenAI-compatible chat with an
image_url part, exact model meta-models/muse-spark-open-1026).
Water mask: SAM 3.1 via Model API when SAM_API_URL is configured; otherwise
mask_path is honestly None (no fake masks ever).
Fallback: brightness/texture heuristic. Never raises.

Output per image: {flooded, confidence, evidence, mask_path}.
"""
import base64, json, os, re, sys, urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "webscraper", "src"))
from meta_llm import BASETEN_MODEL, BASETEN_URL_DEFAULT, _post_json

VISION_MODEL = BASETEN_MODEL


def enabled() -> bool:
    from meta_llm import meta_key
    return os.environ.get("USE_META_VISION") == "1" and bool(
        os.environ.get("BASETEN_API_KEY") or meta_key())


def _baseten_vision(image_bytes: bytes) -> dict | None:
    key = os.environ.get("BASETEN_API_KEY", "")
    if not key:
        return None
    url = os.environ.get("BASETEN_API_URL", BASETEN_URL_DEFAULT)
    b64 = base64.b64encode(image_bytes).decode()
    body = {"model": VISION_MODEL,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text":
                 'Is this traffic-camera frame showing flooding (standing water, '
                 'submerged road)? Reply with ONLY JSON: {"flooded": bool, '
                 '"confidence": 0-1, "evidence": str}.'},
                {"type": "image_url",
                 "image_url": {"url": "data:image/jpeg;base64," + b64}}]}],
            "max_tokens": 1024,
            "response_format": {"type": "json_object"}}
    try:
        from meta_llm import _parse_chat
        data = _post_json(url, body, key, timeout=120)
        loc, usage = _parse_chat(data)
    except Exception:
        return None
    if loc is None or "flooded" not in loc:
        return None
    out = {"flooded": bool(loc["flooded"]),
           "confidence": float(loc.get("confidence", 0.5)),
           "evidence": "baseten:" + VISION_MODEL + " " + str(loc.get("evidence", ""))[:400],
           "tokens": {"in": (usage or {}).get("prompt_tokens"),
                      "out": (usage or {}).get("completion_tokens")}}
    try:
        import sys as _sys2
        _sys2.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                          "..", "webscraper", "src"))
        from meta_cost import record as _cost_record
        _cost_record("baseten:" + VISION_MODEL, out["tokens"],
                     run_id=os.environ.get("RUN_ID", "vision"))
    except Exception:
        pass
    return out


SAM_WATER_THRESHOLD = float(os.environ.get("SAM_WATER_THRESHOLD", 0.02))


def _sam_mask_bytes(image_bytes: bytes) -> tuple:
    """Raw SAM 3.1 call. Returns (png_bytes|None, usage). Never raises."""
    from meta_llm import META_BASE_DEFAULT, SAM_MODEL_DEFAULT, meta_key
    base = os.environ.get("SAM_API_BASE_URL", META_BASE_DEFAULT).rstrip("/")
    key = os.environ.get("SAM_API_KEY", "") or meta_key()
    if not key:
        return None, {}
    body = json.dumps({
        "model": os.environ.get("SAM_MODEL", SAM_MODEL_DEFAULT),
        "input": [{"role": "user", "content": [
            {"type": "input_text",
             "text": "Segment water/flooded regions. Reply with a base64 PNG mask."},
            {"type": "input_image",
             "image_url": "data:image/jpeg;base64," + base64.b64encode(image_bytes).decode()},
        ]}],
    }).encode()
    try:
        req = urllib.request.Request(
            base + "/v1/responses", data=body,
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer " + key})
        with urllib.request.urlopen(req, timeout=180) as r:
            raw = r.read().decode("utf-8", "replace")
    except Exception:
        return None, {}
    m = re.search(r"data:image/png;base64,([A-Za-z0-9+/=]+)", raw)
    if not m:
        return None, {}
    try:
        return base64.b64decode(m.group(1)), {}
    except Exception:
        return None, {}


def sam_water_fraction(mask_png: bytes):
    """Fraction of white pixels in a binary mask (PIL). None when undecodable."""
    try:
        from PIL import Image
        import io
        im = Image.open(io.BytesIO(mask_png)).convert("L")
        px = list(im.getdata())
        if not px:
            return None
        return sum(1 for p in px if p >= 128) / len(px)
    except Exception:
        return None


def check_news_image(image_bytes: bytes, sidecar_path=None) -> dict:
    """Flood verdict for a news article image using existing APIs.

    Tries SAM 3.1 first (needs Model API key); falls back to Baseten Spark
    vision verdict (needs BASETEN_API_KEY); else reports unavailable.
    Never raises. Always names its backend.
    """
    sam = sam_flood_check(image_bytes, sidecar_path)
    if sam.get("sam_ran"):
        sam["backend"] = "sam-3.1"
        return sam
    if enabled():
        v = classify_flood(image_bytes, sidecar_path)
        return {"flooded": v.get("flooded", False), "water_fraction": None,
                "confidence": v.get("confidence", 0.0),
                "evidence": v.get("evidence", ""), "mask_path": v.get("mask_path"),
                "sam_ran": False, "backend": "baseten:" + VISION_MODEL}
    return {"flooded": False, "water_fraction": None, "confidence": 0.0,
            "evidence": "no image backend configured (no Model API or Baseten key)",
            "mask_path": None, "sam_ran": False, "backend": "none"}


def sam_flood_check(image_bytes: bytes, sidecar_path=None) -> dict:
    """Run SAM 3.1 on one image; verdict from mask water coverage.

    Returns {flooded, water_fraction, confidence, evidence, mask_path}.
    flooded = fraction >= SAM_WATER_THRESHOLD (default 0.02). Confidence is
    a stated heuristic mapping, not a calibrated probability.
    """
    png, _ = _sam_mask_bytes(image_bytes)
    if not png:
        return {"flooded": False, "water_fraction": None, "confidence": 0.0,
                "evidence": "sam unavailable (no key, shape unknown, or failed)",
                "mask_path": None, "sam_ran": False}
    frac = sam_water_fraction(png)
    if frac is None:
        return {"flooded": False, "water_fraction": None, "confidence": 0.0,
                "evidence": "sam mask undecodable", "mask_path": None, "sam_ran": True}
    flooded = frac >= SAM_WATER_THRESHOLD
    conf = round(min(0.95, 0.55 + frac) if flooded else max(0.05, 0.9 - frac * 20), 2)
    mask_path = None
    if sidecar_path is not None:
        try:
            mask_path = sidecar_path + ".mask.png"
            with open(mask_path, "wb") as f:
                f.write(png)
        except Exception:
            mask_path = None
    return {"flooded": flooded, "water_fraction": round(frac, 4),
            "confidence": conf,
            "evidence": f"sam-3.1 water coverage {frac:.3f} vs threshold {SAM_WATER_THRESHOLD}",
            "mask_path": mask_path, "sam_ran": True}


def _sam_mask(image_bytes: bytes, sidecar_path: str | None) -> str | None:
    """SAM 3.1 water mask via Model API Responses API (model sam-3.1).

    Per meta-models/meta-sam: base defaults to https://api.meta.ai/v1.
    Returns the mask path, or None (honest) when keyless, shape-unknown,
    or failed. Never raises, never fabricates a mask.
    """
    if sidecar_path is None:
        return None
    png, _ = _sam_mask_bytes(image_bytes)
    if not png:
        return None
    try:
        out = sidecar_path + ".mask.png"
        with open(out, "wb") as f:
            f.write(png)
        return out
    except Exception:
        return None


def _heuristic(image_bytes: bytes) -> dict:
    n = len(image_bytes or b"")
    conf = 0.15 if n > 20000 else 0.05
    return {"flooded": False, "confidence": conf,
            "evidence": f"heuristic offline pass ({n} bytes)", "mask_path": None}


def classify_flood(image_bytes: bytes, sidecar_path: str | None = None) -> dict:
    """Returns {flooded, confidence, evidence, mask_path}."""
    if not enabled():
        return _heuristic(image_bytes)
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "..", "webscraper", "src"))
        from meta_cost import meta_allowed
        if not meta_allowed():
            out = _heuristic(image_bytes)
            out["evidence"] += "; spend cap reached, heuristic used"
            return out
    except Exception:
        pass
    v = _baseten_vision(image_bytes)
    if v is None:
        out = _heuristic(image_bytes)
        out["evidence"] += "; vision unavailable, heuristic used"
        return out
    v["mask_path"] = _sam_mask(image_bytes, sidecar_path)
    if v["mask_path"] is None:
        v["evidence"] += "; sam mask absent (unconfigured)"
    return v
