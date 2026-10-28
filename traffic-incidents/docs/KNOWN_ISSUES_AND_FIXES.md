# Known issues — fixes and status (Track D)

Source: [handover/11_KNOWN_ISSUES.md](../handover/11_KNOWN_ISSUES.md).
Items fixed in `traffic-incidents/` name the file. Items owned by other
tracks carry the recommended fix and were NOT changed here.

## Fixed in this track

1. **Collector never runs (broken cron path).** Old entry
   `cd /home/user/traffic-incident-live-data` — that directory does not
   exist. Fixed: [run.py](run.py) docstring, [README.md](README.md), and
   [scripts/install_cron.sh](scripts/install_cron.sh) use the real path
   `cd /nfs/develop_vinh/traffic-incident-live-data && /home/vinh/.local/bin/uv run python run.py`.
   Observed 2026-10-28: the entry is currently absent from the Paris
   crontab entirely (only the global-rainfall monitor remains), so the
   installer script must still be run on Paris.
2. **Every run re-exported all flood rows ever stored.** Fixed:
   [run.py](run.py) exports only `reported_at >= now - export_lookback_hours`
   (default 6 h, [config/settings.py](config/settings.py)); full history stays
   in the DB. Covered by [tests/test_geojson_export.py](tests/test_geojson_export.py).
3. **`ONE_NETWORK_API_KEY` in settings with no collector.** Fixed: removed
   (with a note to re-add together with a collector if a feed is ever
   specified) — [config/settings.py](config/settings.py),
   [.env.example](.env.example).
4. **Rainfall dir hardcoded via `os.environ` default.** Fixed:
   [enrichment/rainfall.py](enrichment/rainfall.py) reads
   `settings.rainfall_dir` (env/`RAINFALL_DIR`/`.env` overridable); added
   `rainfall_latest_mm` / `rainfall_24h_mm` helpers.
5. **Unrotated/unplaced logs.** Fixed: `LOG_PATH` + `LOG_MAX_BYTES` +
   `LOG_BACKUP_COUNT` settings wired to a `RotatingFileHandler` in
   [run.py](run.py); default is stdout, and the setting documents NFS-only
   (never `/tmp` or root disk).
6. **Naive datetimes / `utcnow`.** No occurrence in this package — all
   timestamps use `datetime.now(timezone.utc)` (collectors, scheduler,
   [run.py](run.py)).

## Documented for owner tracks (not changed here)

7. **Camera state on root disk** (`/tmp/floodwatch_state.json`, traffic-cameras):
   move to NFS (same reason other jobs moved), fsync on write.
8. **Hardcoded Google Custom Search key** (webscraper `src/main_country.py` +
   git history + old log lines): rotate the key, purge history/log lines,
   load from env. No hardcoded key found in this local checkout.
9. **TomTom secrets backup in shared tmp** (`tomtom.env.bak.20261026`): delete
   or move to a private dir, rotate if it was readable.
10. **Stale webscraper README/docs** (entry points `main_2_3.py`/`main_US.py`/…,
    env names, removed daily mains): rewrite from current code; `HANDOVER.md`
    is accurate.
11. **Uncommitted Italy config** (`country_config.py` references removed
    `_RE_EXPLICIT_DATE`; dates now live in `article_signals.py`, English-only):
    review before commit, fix the comment, decide non-English month handling.
12. **`uncertain` cards never adjudicated** (07:40 run can precede
    double-sourcing): add an adjudication pass / delay gate.
13. **Headless permission friction** (blocked Bash heredocs logged as noise;
    Phase 2 cannot run `publish_validated.py`): quiet the filter, document the
    wrapper path.
14. **TomTom**: quiet-day S3 gap (by design — document it); `bbox_geo.py`
    400-bisection claim vs implementation (local `bbox_geo.py:51` does mention
    reactive bisection — verify before changing); `.env.example` eu-west-2 vs
    code eu-west-3 (local code reads eu-west-3); ~100 MB unrotated
    `monitor.log` (add rotation); RainViewer personal-tier licensing decision
    pending; repo has no commits.
15. **Cameras**: `utcnow` deprecation spam, incomplete `SETUP.md`, 8 mm vs
    10 mm threshold doc mismatch, `verify=False` on image downloads (re-enable
    with a CA bundle).
16. **US poller docstring says `uk_flood_rss.py`** (local
    `webscraper/scripts/us_flood_rss.py` docstring is already correct —
    Paris-side only if still present there).
17. **Legacy `cron_run.sh` still exists, unscheduled**: delete or mark
    retired so nobody re-enables its self-posting cards.
18. **Google News 503s (24–26 Oct 2026)**: agency-feed fallback is working as
    designed; no code fix, keep monitoring snippet-fallback rates.
