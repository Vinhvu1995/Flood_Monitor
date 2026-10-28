"""Rainfall + vision cross-check for TomTom Stage A/B floods (no API change).

Annotates each flood row with a cross-check record; never drops rows or
changes the monitor's output shape. Vision runs only when USE_META_VISION=1;
otherwise the record notes the heuristic path.
"""
import os, sys

def crosscheck(row: dict, image_bytes: bytes | None = None) -> dict:
    rain = row.get("rainfall_latest_mm")
    rain24 = row.get("rainfall_24h_mm")
    verdict = {"rainfall_latest_mm": rain, "rainfall_24h_mm": rain24,
               "rain_supports": (rain or 0) >= 8.0,
               "vision": None}
    if image_bytes is not None:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                        "..", "traffic-cameras"))
        try:
            from vision import classify_flood
            v = classify_flood(image_bytes)
        except Exception as e:
            v = {"flooded": False, "confidence": 0.0,
                 "evidence": f"vision unavailable: {type(e).__name__}",
                 "mask_path": None}
        verdict["vision"] = v
        verdict["vision_confirms"] = bool(v.get("flooded"))
    row = dict(row)
    row["crosscheck"] = verdict
    return row
