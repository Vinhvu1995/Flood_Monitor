"""Muse Spark extraction adapter (USE_META_LLM=1).

Thin adapter, not a rewrite: same JSON contract as the OpenAI path
({"flood_event": bool, "locations": [{name, date, context}]}).
Falls back to the heuristic when the flag is off, the key is missing,
or the request fails — quota/host blocks never kill the pipeline.

Env:
  USE_META_LLM=1        enable Spark path
  META_API_URL          Model API chat endpoint (default: Meta Model API)
  META_API_KEY          key (name only in repo; never committed)
  META_MODEL            default: muse-spark (override for Spark Open-1026 etc.)
"""
import json, os, urllib.request

MODEL_DEFAULT = "muse-spark"
API_URL_DEFAULT = "https://api.meta.com/v1/chat/completions"

PROMPT_TMPL = (
    "Decide if the article reports current flooding in {country} between "
    "{start} and {end}. Exclusions: plumbing, forecasts, prevention, historical, "
    "outside dates, warnings, outside country, advisories, service notices. "
    "Extract explicitly named places at most specific granularity. "
    'Output {"flood_event": bool, "locations": [{"name","date","context"}]}.\n'
)

def enabled() -> bool:
    return os.environ.get("USE_META_LLM") == "1" and bool(os.environ.get("META_API_KEY"))

def extract(text: str, country: str, start: str, end: str,
            model: str | None = None) -> dict | None:
    """Returns parsed dict, or None when the caller should use fallback."""
    if not enabled():
        return None
    body = json.dumps({
        "model": model or os.environ.get("META_MODEL", MODEL_DEFAULT),
        "messages": [{"role": "user",
                      "content": PROMPT_TMPL.format(country=country, start=start,
                                                    end=end) + text[:6000]}],
        "response_format": {"type": "json_object"},
    }).encode()
    url = os.environ.get("META_API_URL", API_URL_DEFAULT)
    try:
        req = urllib.request.Request(
            url, data=body,
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer " + os.environ["META_API_KEY"]})
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
        content = data["choices"][0]["message"]["content"]
        loc = json.loads(content)
        usage = data.get("usage", {})
        return {"flood_event": bool(loc.get("flood_event")),
                "locations": loc.get("locations", []),
                "meta_model": model or os.environ.get("META_MODEL", MODEL_DEFAULT),
                "meta_tokens": {"in": usage.get("prompt_tokens"),
                                "out": usage.get("completion_tokens")}}
    except Exception:
        return None
