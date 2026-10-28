"""Client-side Meta/partner spend accumulator (Model API has no usage API).

Every LLM/vision call records {model, in, out, cost, run_id} to
cron_logs/meta_cost.jsonl; pre-run check aborts the Meta path near caps.
Dashboards remain source of truth: dev.meta.ai/usage, Baseten billing page.

Rates (USD/1M tokens) overridable by env for refreshes without code edits:
  SPARK_IN=1.25 SPARK_OUT=4.25 SPARK_CACHED_IN=0.15 (Model API Standard)
  BASETEN_SMO_IN/BASETEN_SMO_OUT default to Spark Standard until Baseten
  publishes open-weights metered rates — costs are estimates, caps enforced
  on Baseten's own credits_used figure from check_credits.sh.
"""
import json, os, time

COST_LOG = os.environ.get("META_COST_LOG",
                          os.path.join("cron_logs", "meta_cost.jsonl"))

RATES = {
    "spark": (float(os.environ.get("SPARK_IN", 1.25)),
              float(os.environ.get("SPARK_OUT", 4.25))),
    "baseten": (float(os.environ.get("BASETEN_SMO_IN", 1.25)),
                float(os.environ.get("BASETEN_SMO_OUT", 4.25))),
}

META_CAP_USD = float(os.environ.get("META_CAP_USD", 150.0))
BASETEN_CAP_USD = float(os.environ.get("BASETEN_CAP_USD", 100.0))


def cost_usd(model: str, n_in, n_out) -> float:
    key = "baseten" if "baseten" in (model or "") or "muse-spark-open" in (model or "") else "spark"
    r_in, r_out = RATES[key]
    return ((n_in or 0) / 1e6) * r_in + ((n_out or 0) / 1e6) * r_out


def record(model: str, tokens: dict | None, run_id: str = "") -> dict:
    tokens = tokens or {}
    entry = {"ts": int(time.time()), "model": model,
             "in": tokens.get("in"), "out": tokens.get("out"),
             "cost": round(cost_usd(model, tokens.get("in"), tokens.get("out")), 4),
             "run_id": run_id}
    os.makedirs(os.path.dirname(COST_LOG), exist_ok=True)
    with open(COST_LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")
    return entry


def spent_since(path: str | None = None) -> float:
    total = 0.0
    try:
        with open(path or COST_LOG) as f:
            for line in f:
                try:
                    total += float(json.loads(line).get("cost", 0) or 0)
                except (ValueError, TypeError):
                    continue
    except FileNotFoundError:
        pass
    return round(total, 4)


def meta_allowed(extra_in=200000, extra_out=10000) -> bool:
    """False when projected client-side spend reaches the $150 cap."""
    return spent_since() < META_CAP_USD
