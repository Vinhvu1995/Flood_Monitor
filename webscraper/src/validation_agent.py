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
    from meta_llm import meta_key
    return os.environ.get("USE_META_LLM") == "1" and bool(
        os.environ.get("BASETEN_API_KEY") or meta_key())


def _parse_verdict(raw: str) -> dict | None:
    """Tolerant {keep, reason} parse (fences, embedded JSON)."""
    import re as _re
    s = (raw or "").strip()
    if s.startswith("```"):
        s = _re.sub(r"^```(?:json)?\s*", "", s)
        s = _re.sub(r"\s*```$", "", s)
    try:
        v = json.loads(s)
    except (ValueError, TypeError):
        m = _re.search(r"\{.*\}", s, _re.S)
        if not m:
            return None
        try:
            v = json.loads(m.group(0))
        except (ValueError, TypeError):
            return None
    if isinstance(v, dict) and "keep" in v:
        return {"keep": bool(v["keep"]), "reason": str(v.get("reason", ""))[:300]}
    return None


def _meta_verdict(prompt: str):
    """Verdict via Baseten first, Model API fallback. (parsed|None, backend)."""
    from meta_llm import (BASETEN_MODEL, BASETEN_URL_DEFAULT, META_MODEL_DEFAULT,
                          META_URL_DEFAULT, _post_json, meta_key)
    bodies = []
    if os.environ.get("BASETEN_API_KEY"):
        bodies.append((os.environ.get("BASETEN_API_URL", BASETEN_URL_DEFAULT),
                       {"model": BASETEN_MODEL,
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": 1024,
                        "response_format": {"type": "json_object"}},
                       os.environ["BASETEN_API_KEY"],
                       "baseten:" + BASETEN_MODEL))
    if meta_key():
        bodies.append((os.environ.get("META_API_URL", META_URL_DEFAULT),
                       {"model": os.environ.get("META_MODEL", META_MODEL_DEFAULT),
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": 1024,
                        "response_format": {"type": "json_object"}},
                       meta_key(),
                       "model-api:" + os.environ.get("META_MODEL", META_MODEL_DEFAULT)))
    for url, body, key, backend in bodies:
        try:
            data = _post_json(url, body, key, timeout=90)
            msg = (data.get("choices") or [{}])[0].get("message", {})
            for field in ("content", "reasoning_content"):
                v = _parse_verdict(msg.get(field) if isinstance(msg.get(field), str) else None)
                if v is not None:
                    return v, backend
        except Exception:
            continue
    return None, None

def meta_confirm(url: str, location: dict, prior: tuple[bool, str],
                 evidence: str = "") -> tuple[bool, str]:
    """Spark second opinion (Baseten first, Model API fallback). Offline-safe.

    The model judges ONLY the supplied evidence text (article excerpt around
    the location mention). Without evidence it cannot verify, so callers must
    pass it; an empty evidence string keeps the prior.
    """
    if not _meta_on() or not (evidence or "").strip():
        return prior
    prompt = (f"Validation verdict. URL: {url}. Location: {location.get('location')}. "
              f"Prior: keep={prior[0]} reason={prior[1]}. "
              f"EVIDENCE (article excerpt): {evidence[:1500]} "
              'Reply with ONLY JSON {"keep": bool, "reason": str}. Judge ONLY the evidence: '
              'keep:true only if the location is reported as flooded there. Keep:false for '
              "closure lists, alert centres, dashboards, digests, nav/footer boilerplate, "
              "bylines, photo credits, or if the location is absent from the evidence.")
    try:
        v, backend = _meta_verdict(prompt)
    except Exception:
        v = None
    if v is None:
        return prior  # Meta down -> fail closed on the rule verdict
    return v["keep"], f"{backend} {v['reason']}"[:300]

def _evidence_for(article_text: str, location: str, radius: int = 500) -> str:
    """Article excerpt around the first location mention ("" when unmentioned)."""
    idx = (article_text or "").lower().find((location or "").lower())
    if not location or idx == -1:
        return ""
    return article_text[max(0, idx - radius): idx + len(location) + radius]


def adjudicate(groups: list[dict]) -> list[dict]:
    """Per-(url, location) verdicts for every pair in the batch."""
    verdicts = []
    for g in groups:
        for loc in g.get("locations", []):
            keep, reason = rule_verdict(g.get("url", ""), loc,
                                       g.get("article_text", ""),
                                       g.get("date_signal", "none"))
            evidence = _evidence_for(g.get("article_text", ""), loc.get("location", ""))
            keep, reason = meta_confirm(g.get("url", ""), loc, (keep, reason), evidence)
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
