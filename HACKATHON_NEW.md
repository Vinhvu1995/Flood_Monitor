# Flood Monitor — Hackathon Work (Oct 27 – Nov 4, 2026)

Startup track, Push the Frontier. Plan: `HACKATHON_PLAN.md`.
This file separates hackathon-period work from the pre-existing scaffold.
Teams alerting was removed per owner decision — all alerts are local logs
(`cron_logs/notifications.log`, `state/flood_alerts.log`), never webhooks.

## New in hackathon period
- `webscraper/src/meta_llm.py` — Spark Open-1026 extraction via Baseten
  (exact model `meta-models/muse-spark-open-1026`, `content`+`reasoning_content`
  parsing, strict JSON) behind `USE_META_LLM=1`; Model API Spark fallback,
  then heuristic. Model/tokens/cost land in `run_summary.json`.
- `webscraper/src/meta_cost.py` + `scripts/check_credits.sh` — client-side
  spend accumulator (`cron_logs/meta_cost.jsonl`, caps $150 Meta / $100
  Baseten) and Baseten auto-check that fails soft to heuristics, never leaks keys.
- `webscraper/src/validation_agent.py` — rule pass + Meta confirm loop,
  per-`(url, location)` verdicts, SHA-256 receipt before publish.
- `traffic-cameras/vision.py` — `classify_flood()` via Baseten vision behind
  `USE_META_VISION=1`; SAM 3.1 mask via Model API when `SAM_API_URL` is set,
  otherwise honest `mask_path=None`; `{flooded,confidence,evidence,mask}`
  saved alongside `images/` + `geojson/`.
- `tomtom-alerts/crosscheck.py` — rainfall + vision cross-check on Stage A/B
  outputs, no API-shape change.
- RSS-first discovery — full Paris feed lists (73 UK + 174 US) as single source
  of truth in `src/sources.py`; keyless production default (RSS+GDELT+GNews);
  date filtering, namespace stripping, dead Met Office URL replaced.
- Offline extraction fallback — proper-noun candidates with storm-name and
  site-chrome gates, so quota/host blocks never kill a run.
- `scripts/pull_paris.sh`, `scripts/pull_s3.sh` — bounded read-only pulls from
  Paris and S3 into `_napoleon_data/` (secrets and bulk caches excluded).
- `_napoleon_data/` — pulled reference data (git-ignored sample of live outputs).

## Flags
- `USE_META_LLM=1` — Baseten Spark Open-1026 news extraction (default: fallback chain).
- `USE_META_VISION=1` — Baseten vision camera classification (default: heuristic).

## Keys (names only — never committed)
- `BASETEN_API_KEY` (Baseten, exact model `meta-models/muse-spark-open-1026`).
- `META_API_KEY` (Model API Spark direct + SAM 3.1; `SAM_API_URL` when known).
- `TOMTOM_API_KEY`, `OPENAI_API_KEY` (fallback only).
