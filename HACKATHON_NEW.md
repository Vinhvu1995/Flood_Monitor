# Flood Monitor — Hackathon Work (Oct 27 – Nov 4, 2026)

Startup track, Push the Frontier. Plan: `HACKATHON_PLAN.md`.
This file separates hackathon-period work from the pre-existing scaffold.
Teams alerting was removed per owner decision — all alerts are local logs.

## New in hackathon period
- `webscraper/src/meta_llm.py` — Muse Spark extraction adapter behind
  `USE_META_LLM=1`, OpenAI/heuristic fallback, model+tokens in `run_summary.json`.
- `webscraper/src/validation_agent.py` — Spark tool-calling validation loop,
  per-`(url, location)` verdicts, SHA-256 receipt before publish.
- `traffic-cameras/vision.py` — `classify_flood()` via Spark vision + SAM 3.1
  water mask behind `USE_META_VISION=1`; `{flooded,confidence,evidence,mask}`
  saved alongside `images/` + `geojson/`.
- `tomtom-alerts/crosscheck.py` — rainfall + vision cross-check on Stage A/B
  outputs, no API-shape change.
- `scripts/pull_paris.sh`, `scripts/pull_s3.sh` — bounded read-only pulls from
  Paris and S3 into `_napoleon_data/` (secrets and bulk caches excluded).
- `_napoleon_data/` — pulled reference data (git-ignored sample of live outputs).

## Scaffold (pre-existing, kept intact)
- `webscraper/` pipeline (`main_country.py`), country configs, sources,
  signals, LSR parser, geocoding tiers, validation receipt, RSS pollers,
  world digest, quota monitor, `daily_monitoring_agent.sh`, `agent_daily_run.sh`.
- `tomtom-alerts/` storm sessions, radar/ASC cells, monitor, credit health.
- `traffic-cameras/` rainfall trigger, discovery toolchain.
- `traffic-incidents/` road-authority collectors, storage, enrichment.
- `handover/` review docs (removed before public push).

## Flags
- `USE_META_LLM=1` — Spark news extraction (default: OpenAI/heuristic fallback).
- `USE_META_VISION=1` — Spark vision + SAM 3.1 camera classification
  (default: heuristic fallback, pipeline never breaks).

## Keys (names only — never committed)
- `META_API_KEY` (Model API, Spark), `BASETEN_API_KEY` (Spark Open/Glimmer),
  `TOMTOM_API_KEY`, `OPENAI_API_KEY` (fallback only).
