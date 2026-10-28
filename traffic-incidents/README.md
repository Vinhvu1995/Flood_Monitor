# Traffic incident live-data collector

Single-run collector (`run.py`, the cron entry) plus an APScheduler
alternative (`scheduler/poll.py`). Fetches flood-related road incidents from
four UK sources, upserts on `(source, external_id)`, enriches with NFS
rainfall, and uploads a GeoParquet snapshot to S3.

## Install

```bash
cp .env.example .env   # then fill in NH_API_KEY / TFL_APP_KEY
uv sync
```

## Run

```bash
uv run python run.py                 # single run (cron entry)
uv run python -m scheduler.poll --dry-run  # fetch once, print, do not store
uv run python -m scheduler.poll      # long-running poller
pytest                               # tests
```

## Cron (fixed path)

The old entry `cd /home/user/traffic-incident-live-data` pointed at a
directory that does not exist, so the job failed every run. Fixed entry
(see [scripts/install_cron.sh](scripts/install_cron.sh)):

```
*/2 * * * * cd /nfs/develop_vinh/traffic-incident-live-data && /home/vinh/.local/bin/uv run python run.py >> cron.log 2>&1
```

## Layout

| Path | Purpose |
|---|---|
| [run.py](run.py) | single-run collector (cron entry) |
| [config/settings.py](config/settings.py) | pydantic-settings from `.env` |
| [models/incident.py](models/incident.py) | `Incident`, `IncidentSource`, `FloodSeverity` |
| [collectors/](collectors/) | national_highways (DATEX II), tfl, traffic_scotland, traffic_watch_ni |
| [storage/db.py](storage/db.py) | `IncidentRow`, `init_db`, `upsert_incidents` |
| [storage/geojson_export.py](storage/geojson_export.py) | `export_geojson(start, end, flood_only)` |
| [enrichment/rainfall.py](enrichment/rainfall.py) | `enrich`, `rainfall_latest_mm`, `rainfall_24h_mm` |
| [scheduler/poll.py](scheduler/poll.py) | APScheduler alternative (`--dry-run` supported) |
| [tests/](tests/) | `test_models.py`, `test_collectors.py`, `test_db.py`, `test_geojson_export.py` |

Flood keywords: flood, flooding, surface water, standing water, submerged.

Per-run S3 uploads cover only incidents reported within
`export_lookback_hours` (default 6 h) — full history is kept in the DB but
never re-uploaded each run. Uploads go to
`s3://previsico-modelling-team/Traffic_Incidents/Flood_Incidents/<YYYY-MM-DD>/<ts>/`
(region eu-west-3). `run.py` exits 1 if any collector raised.

See [docs/KNOWN_ISSUES_AND_FIXES.md](docs/KNOWN_ISSUES_AND_FIXES.md) for the
review findings and what was fixed here.
