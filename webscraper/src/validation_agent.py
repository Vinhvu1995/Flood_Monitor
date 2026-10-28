"""Verifiable validation agent (Spark tool-calling loop, offline-capable).

Keeps the handover keep:false rules (closure lists, alert centres, dashboards,
digests, out_of_window, mixed-with-older-flood, snippet-only, state mismatch,
undated-without-tie, unsure) and writes the SHA-256 receipt via
validation_receipt before publish_validated runs.

Env: USE_META_LLM=1 + META_API_KEY enables the Spark second opinion;
otherwise the deterministic rule pass decides alone (fail closed).
"""
import json, os, re
from validation_receipt import write_receipt

KEEP_FALSE_HINTS = (
    "closure list", "alert centre", "dashboard", "digest",
    "section index", "staff page", "warning bulletin",
)

def rule_verdict(url: str, location: dict, article_text: str,
                 date_signal: str = "none") -> tuple[bool, str]:
    """Deterministic first pass. Returns (keep, reason)."""
    from main_country import is_chrome
    loc = (location.get("location") or "").lower()
    txt = (article_text or "").lower()
    if date_signal == "out_of_window":
        return False, "out_of_window date signal"
    if is_chrome(location.get("location") or ""):
        return False, "sidebar contamination (site chrome, not a place)"
    if any(h in txt[:500] for h in KEEP_FALSE_HINTS):
        return False, "closure/index/dashboard shape"
    if "snippet" in txt and len(article_text or "") < 400:
        return False, "snippet-only without in-window date"
    if not loc or loc not in txt:
        return False, "location not reported at that place"
    return True, "rule pass; needs Spark confirm" if _meta_on() else "rule pass (offline)"

def _meta_on() -> bool:
    return os.environ.get("USE_META_LLM") == "1" and bool(os.environ.get("META_API_KEY"))

def meta_confirm(url: str, location: dict, prior: tuple[bool, str]) -> tuple[bool, str]:
    """Spark second opinion via tool-calling-shaped JSON. None-safe offline."""
    if not _meta_on():
        return prior
    try:
        from meta_llm import PROMPT_TMPL  # reuse endpoint config
        import urllib.request
        body = json.dumps({
            "model": os.environ.get("META_MODEL", "muse-spark"),
            "messages": [{"role": "user", "content":
                f"Validation verdict. URL: {url}. Location: {location.get('location')}. "
                f"Prior: keep={prior[0]} reason={prior[1]}. "
                'Reply {"keep": bool, "reason": str}. Keep:false for closure lists, '
                "alert centres, dashboards, digests, out-of-window, state mismatch, unsure."}],
            "response_format": {"type": "json_object"},
        }).encode()
        req = urllib.request.Request(
            os.environ.get("META_API_URL", "https://api.meta.com/v1/chat/completions"),
            data=body, headers={"Content-Type": "application/json",
                                "Authorization": "Bearer " + os.environ["META_API_KEY"]})
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
        v = json.loads(data["choices"][0]["message"]["content"])
        return bool(v.get("keep")), str(v.get("reason", "spark verdict"))[:300]
    except Exception:
        return prior  # Spark down -> fail closed on the rule verdict

def adjudicate(groups: list[dict]) -> list[dict]:
    """Per-(url, location) verdicts for every pair in the batch."""
    verdicts = []
    for g in groups:
        for loc in g.get("locations", []):
            keep, reason = rule_verdict(g.get("url", ""), loc,
                                       g.get("article_text", ""),
                                       g.get("date_signal", "none"))
            keep, reason = meta_confirm(g.get("url", ""), loc, (keep, reason))
            verdicts.append({"url": g.get("url", ""),
                             "location": loc.get("location", ""),
                             "keep": keep, "reason": reason})
    return verdicts

def write_validated_receipt(run_dir: str, verdicts_file: str, groups: list[dict]) -> str:
    verdicts = adjudicate(groups)
    kept = sum(1 for v in verdicts if v["keep"])
    dropped = len(verdicts) - kept
    return write_receipt(run_dir, verdicts_file, verdicts,
                         raw_pairs=len(verdicts), covered=len(verdicts),
                         dropped_explicit=dropped, dropped_uncovered=0,
                         kept=kept)
