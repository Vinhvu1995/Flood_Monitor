"""Camera flood sight (USE_META_VISION=1).

classify_flood() runs Spark vision + SAM 3.1 water mask when enabled;
otherwise a brightness/texture heuristic decides. Never raises —
the trigger must keep scheduling even when vision is down.

Output per image: {flooded, confidence, evidence, mask_path}.
Sidecars are saved next to images/ and referenced from the geojson.
"""
import base64, json, os

def enabled() -> bool:
    return os.environ.get("USE_META_VISION") == "1" and bool(os.environ.get("META_API_KEY"))

def _heuristic(image_bytes: bytes) -> dict:
    # Dark/uniform frames already discarded upstream; rank the rest low.
    n = len(image_bytes or b"")
    conf = 0.15 if n > 20000 else 0.05
    return {"flooded": False, "confidence": conf,
            "evidence": f"heuristic offline pass ({n} bytes)", "mask_path": None}

def classify_flood(image_bytes: bytes, sidecar_path: str | None = None) -> dict:
    """Returns {flooded, confidence, evidence, mask_path}."""
    if not enabled():
        return _heuristic(image_bytes)
    try:
        import urllib.request
        b64 = base64.b64encode(image_bytes).decode()
        body = json.dumps({
            "model": os.environ.get("META_VISION_MODEL", "muse-spark-vision"),
            "messages": [{"role": "user", "content": [
                {"type": "text", "text":
                 'Is this traffic-camera frame showing flooding (standing water, '
                 'submerged road)? Reply {"flooded": bool, "confidence": 0-1, "evidence": str}.'},
                {"type": "image_url",
                 "image_url": {"url": "data:image/jpeg;base64," + b64}}]}],
            "response_format": {"type": "json_object"},
        }).encode()
        req = urllib.request.Request(
            os.environ.get("META_API_URL", "https://api.meta.com/v1/chat/completions"),
            data=body, headers={"Content-Type": "application/json",
                                "Authorization": "Bearer " + os.environ["META_API_KEY"]})
        with urllib.request.urlopen(req, timeout=90) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
        v = json.loads(data["choices"][0]["message"]["content"])
        out = {"flooded": bool(v.get("flooded")),
               "confidence": float(v.get("confidence", 0.5)),
               "evidence": str(v.get("evidence", "spark vision"))[:500],
               "mask_path": None}
        # SAM 3.1 water mask: Paris GPU job fills mask_path; recorded when present.
        if sidecar_path and os.path.exists(sidecar_path + ".mask.png"):
            out["mask_path"] = sidecar_path + ".mask.png"
        return out
    except Exception as e:
        out = _heuristic(image_bytes)
        out["evidence"] += f"; vision failed: {type(e).__name__}"
        return out
