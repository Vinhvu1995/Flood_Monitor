# Flood Monitor — Hackathon Plan (Push the Frontier)

Source guide: Meta Global AI Developer Hackathon Participant Guide (saved 2026-10-28, now removed from this repo per cleanup).
Flood Monitor context: `/home/vinh/Flood_Monitor/handover/*.md` + scaffolds in `/home/vinh/Flood_Monitor/{webscraper,tomtom-alerts,traffic-cameras,traffic-incidents}/`.
Deadline: Nov 4 9pm ET. Building period: Oct 27 – Nov 4.

## Goal
Ship a Push the Frontier Flood Monitor entry by Nov 4 9pm ET that keeps the Paris flood pipeline but makes Meta models load-bearing: Muse Spark for news extraction/validation, Spark vision + SAM 3.1 for traffic-camera flood seeing, Spark Open/Glimmer via partner inference for the open-weights bonus.

## Success Criteria
- Stage 1 pass: project reasonably uses Meta Model API and/or Meta open-weights, fits Push the Frontier.
- Public repo with `HACKATHON_NEW.md` clearly separating Oct 27-Nov 4 work from scaffold.
- One-command offline tests pass; one live dry-run writes vectors + Teams-card payload without posting to live webhook.
- 3-min demo video + code + live product ready for CV platform, Representative assigned.

## Approach
Adapter, not rewrite. Keep `main_country.py` pipeline, storm-session logic, and `rainfall_trigger.py` scheduling intact. Add thin `meta_*` adapters behind env flags with OpenAI/heuristic fallback so quota or host blocks never kill the demo. Prioritize camera vision — that is the new physical capability — then news extraction, then verifiable validation agent. Target Best Agentic and Best Open-Weights bonuses with the same work.

## Steps
1. Setup + guardrails: confirm $150 Model API credit, Billing spend limit, `.env` keys only by name; add `HACKATHON_NEW.md`, `hackathon/` branch; wire `USE_META_LLM=1`, `USE_META_VISION=1` flags.
2. News extraction on Spark: add `meta_extract.py` adapter in `webscraper/src/main_country.py` replacing `llm_params()`/`generate_jsonl()`/`analyze_text()` path for `gpt-5-nano`; map `response_format=json_object` + reasoning effort to Spark/Spark Open-1026 via Baseten; keep OpenAI fallback; log model/tokens in `run_summary.json`.
3. Verifiable validation agent: upgrade Phase 2 `prepare_validation_batch.py` + `apply_validation.py` + `src/validation_receipt.py` to Spark tool-calling loop with per-`(url,location)` verdicts, keep existing `keep:false` rules, write SHA-256 receipt before `publish_validated.py`.
4. Camera flood sight: extend `traffic-cameras/rainfall_trigger.py` post-download with `classify_flood()` via Spark vision + SAM 3.1 water mask; save `{flooded,confidence,evidence,mask}` alongside `images/` + `geojson/` and S3; feed into Teams card text.
5. TomTom + incidents light touch: add rainfall + vision cross-check to `tomtom-alerts/uk_incident_monitor.py` Stage A/B outputs, no API-shape change; leave broken `traffic-incident-live-data` cron path out of demo.
6. Demo + submission: public repo check, 3-min video showing code diff, live run, vectors/cards; post to X for Most Viral, submit link + gallery vote.

## Validation Plan
- `python -m pytest webscraper/tests/ traffic-cameras/tests/ -q` — expect green offline.
- Dry runs: `USE_META_LLM=1 webscraper/scripts/agent_daily_run.sh US <date> 2 --dry-run`, `rainfall_trigger.py --test-asc <file> --dry-run`, `send_teams_card.py` against `mock_teams_server.py` — check `run_summary.json`, `validation receipt`, S3-deferred vector paths, no live post.
- Manual: open one camera image + SAM mask + card payload side-by-side for video.

## Risks / Open Questions
- Risk: EU-host blocks, Google 429/503, S3 `PutObject` denied on interactive role — mitigate with RSS-first discovery, fallback model flag, `ALLOW_RAW_S3_VECTORS` deferred upload.
- Risk: Baseten `muse-spark-open-1026` naming vs docs `muse-spark-open` — use 1026 string.
- Open: which track — Indie vs Startup — decides Representative and prize table; confirm before submitting.
- Non-goals: no medical/education/coach features, no TikTok unblocking, no rewrite of geocoders/rasters.

## Token budget note (2026-10-28)
- Spark Standard: $1.25/1M in, $4.25/1M out (~120M in-only, ~35M out-only, ~54-81M mixed on $150).
- Contributor: $0.10/$0.20 (~1.2B mixed) but data may train models.
- Wonder: $4/$20. Glimmer: open weights, infra cost only.
