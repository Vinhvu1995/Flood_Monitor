"""Muse Spark Open-1026 via Baseten (primary) + Model API Spark (fallback).

Primary: OpenAI-compatible chat completions at
  base_url=https://inference.baseten.co/v1
  model=meta-models/muse-spark-open-1026 (exact; the key works only with it)
Spark Open always reasons: read BOTH `content` and `reasoning_content`
(a low max_tokens run returned null `content`), generous `max_tokens`,
strict-ish JSON extraction.

Fallback order: Baseten -> Model API Spark direct -> heuristic.
Quota/host blocks never kill the pipeline: every failure returns None and
the caller falls through.

Env:
  USE_META_LLM=1        enable Meta path
  BASETEN_API_KEY       Baseten key (.env only, never committed)
  BASETEN_API_URL       default https://inference.baseten.co/v1/chat/completions
  META_API_KEY          Model API key (fallback)
  META_API_URL          default https://api.meta.ai/v1/chat/completions
  META_MODEL            default muse-spark
"""
import json, os, re, urllib.request

BASETEN_MODEL = "meta-models/muse-spark-open-1026"
BASETEN_URL_DEFAULT = "https://inference.baseten.co/v1/chat/completions"
META_MODEL_DEFAULT = "muse-spark"
META_URL_DEFAULT = "https://api.meta.ai/v1/chat/completions"
META_BASE_DEFAULT = "https://api.meta.ai/v1"
SAM_MODEL_DEFAULT = "sam-3.1"


def meta_key() -> str:
    """Meta key: META_API_KEY first, MODEL_API_KEY alias (Meta's documented name)."""
    return os.environ.get("META_API_KEY", "") or os.environ.get("MODEL_API_KEY", "")

PROMPT_TMPL = (
    "Decide if the article reports current flooding in {country} between "
    "{start} and {end}. Exclusions: plumbing, forecasts, prevention, historical, "
    "outside dates, warnings, outside country, advisories, service notices. "
    "Extract explicitly named places at most specific granularity. "
    'Reply with ONLY this JSON object: {{"flood_event": bool, '
    '"locations": [{{"name": str, "date": str, "context": str}}]}}.\n'
)


def _post_json(url: str, body: dict, key: str, timeout: int = 90) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + key})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _parse_chat(data: dict) -> tuple[dict | None, dict]:
    """Return (parsed_json_or_None, usage). Reads content + reasoning_content."""
    try:
        msg = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        return None, {}
    usage = data.get("usage", {}) or {}
    for field in ("content", "reasoning_content"):
        raw = msg.get(field)
        if not raw or not isinstance(raw, str):
            continue
        loc = _extract_json(raw)
        if loc is not None:
            return loc, usage
    return None, usage


def _extract_json(raw: str) -> dict | None:
    s = raw.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    try:
        loc = json.loads(s)
    except (json.JSONDecodeError, ValueError):
        m = re.search(r"\{.*\}", s, re.S)
        if not m:
            return None
        try:
            loc = json.loads(m.group(0))
        except (json.JSONDecodeError, ValueError):
            return None
    if isinstance(loc, dict) and "flood_event" in loc:
        loc.setdefault("locations", [])
        return loc
    return None


def _baseten_extract(prompt: str) -> tuple[dict | None, dict]:
    key = os.environ.get("BASETEN_API_KEY", "")
    if not key:
        return None, {}
    url = os.environ.get("BASETEN_API_URL", BASETEN_URL_DEFAULT)
    body = {"model": BASETEN_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 4096,
            "response_format": {"type": "json_object"}}
    try:
        data = _post_json(url, body, key)
    except Exception:
        return None, {}
    return _parse_chat(data)


def _meta_fallback_extract(prompt: str) -> tuple[dict | None, dict]:
    key = meta_key()
    if not key:
        return None, {}
    url = os.environ.get("META_API_URL", META_URL_DEFAULT)
    body = {"model": os.environ.get("META_MODEL", META_MODEL_DEFAULT),
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 2048,
            "response_format": {"type": "json_object"}}
    try:
        data = _post_json(url, body, key)
    except Exception:
        return None, {}
    return _parse_chat(data)


def enabled() -> bool:
    return os.environ.get("USE_META_LLM") == "1" and bool(
        os.environ.get("BASETEN_API_KEY") or meta_key())


def extract(text: str, country: str, start: str, end: str) -> dict | None:
    """Returns parsed dict (with meta_model/meta_tokens), or None to fall back."""
    if os.environ.get("USE_META_LLM") != "1":
        return None
    try:
        from meta_cost import meta_allowed
        if not meta_allowed():
            return None  # cap reached: caller falls back to heuristics
    except Exception:
        pass
    prompt = PROMPT_TMPL.format(country=country, start=start,
                                end=end) + text[:6000]
    loc, usage = _baseten_extract(prompt)
    backend = "baseten:" + BASETEN_MODEL
    if loc is None:
        loc, usage = _meta_fallback_extract(prompt)
        backend = "model-api:" + os.environ.get("META_MODEL", META_MODEL_DEFAULT)
    if loc is None:
        return None
    loc["flood_event"] = bool(loc.get("flood_event"))
    loc["meta_model"] = backend
    loc["meta_tokens"] = {"in": usage.get("prompt_tokens"),
                          "out": usage.get("completion_tokens")}
    return loc
