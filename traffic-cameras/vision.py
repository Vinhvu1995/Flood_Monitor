"""Camera flood sight (USE_META_VISION=1).

Primary: Spark Open-1026 vision via Baseten (OpenAI-compatible chat with an
image_url part, exact model meta-models/muse-spark-open-1026).
Water mask: SAM 3.1 via Model API when SAM_API_URL is configured; otherwise
mask_path is honestly None (no fake masks ever).
Fallback: brightness/texture heuristic. Never raises.

Output per image: {flooded, confidence, evidence, mask_path}.
"""
import base64, json, os, sys, urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "webscraper", "src"))
from meta_llm import BASETEN_MODEL, BASETEN_URL_DEFAULT, _post_json

VISION_MODEL = BASETEN_MODEL


def enabled() -> bool:
    return os.environ.get("USE_META_VISION") == "1" and bool(
        os.environ.get("BASETEN_API_KEY") or os.environ.get("META_API_KEY"))


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
    return {"flooded": bool(loc["flooded"]),
            "confidence": float(loc.get("confidence", 0.5)),
            "evidence": "baseten:" + VISION_MODEL + " " + str(loc.get("evidence", ""))[:400],
            "tokens": {"in": (usage or {}).get("prompt_tokens"),
                       "out": (usage or {}).get("completion_tokens")}}


def _sam_mask(image_bytes: bytes, sidecar_path: str | None) -> str | None:
    """SAM 3.1 water mask via Model API. None when unconfigured or failed."""
    url = os.environ.get("SAM_API_URL", "")
    key = os.environ.get("SAM_API_KEY", "") or os.environ.get("META_API_KEY", "")
    if not url or not key or sidecar_path is None:
        return None
    try:
        req = urllib.request.Request(
            url, data=image_bytes,
            headers={"Content-Type": "image/jpeg",
                     "Authorization": "Bearer " + key})
        with urllib.request.urlopen(req, timeout=120) as r:
            mask = r.read()
        if len(mask) < 100:
            return None
        out = sidecar_path + ".mask.png"
        with open(out, "wb") as f:
            f.write(mask)
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
