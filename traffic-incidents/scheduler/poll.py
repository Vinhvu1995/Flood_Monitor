"""
Polling scheduler — runs all traffic collectors on configured intervals
and upserts results into the database.

Usage:
    python -m scheduler.poll            # run continuously
    python -m scheduler.poll --dry-run  # fetch once, print results, do not store
"""
from __future__ import annotations

import argparse
import logging
from datetime import datetime, timezone

from apscheduler.schedulers.blocking import BlockingScheduler

from collectors import national_highways, tfl, traffic_scotland, traffic_watch_ni
from config.settings import settings
from storage.db import init_db, upsert_incidents

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


def _run_collector(name: str, fetch_fn):
    log.info("[%s] polling...", name)
    try:
        incidents = fetch_fn()
        inserted = upsert_incidents(incidents)
        log.info("[%s] %d fetched, %d new", name, len(incidents), inserted)
    except Exception as exc:
        log.error("[%s] error: %s", name, exc)


def dry_run():
    collectors = [
        ("national_highways", national_highways.fetch),
        ("tfl", tfl.fetch),
        ("traffic_scotland", traffic_scotland.fetch),
        ("traffic_watch_ni", traffic_watch_ni.fetch),
    ]
    for name, fetch_fn in collectors:
        log.info("[%s] dry-run fetch...", name)
        try:
            incidents = fetch_fn()
            log.info("[%s] returned %d flood incidents", name, len(incidents))
            for inc in incidents[:3]:
                log.info("  %s | %s | %s | %s", inc.external_id, inc.road_ref, inc.reported_at, inc.description[:80])
        except Exception as exc:
            log.error("[%s] error: %s", name, exc)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Fetch once and print; do not store")
    args = parser.parse_args()

    if args.dry_run:
        dry_run()
        return

    init_db()
    scheduler = BlockingScheduler()

    for name, fetch_fn, interval in [
        ("national_highways", national_highways.fetch, settings.poll_interval_national_highways),
        ("tfl", tfl.fetch, settings.poll_interval_tfl),
        ("traffic_scotland", traffic_scotland.fetch, settings.poll_interval_traffic_scotland),
        ("traffic_watch_ni", traffic_watch_ni.fetch, settings.poll_interval_traffic_watch_ni),
    ]:
        scheduler.add_job(
            _run_collector,
            "interval",
            seconds=interval,
            args=[name, fetch_fn],
            next_run_time=datetime.now(timezone.utc),
        )

    log.info("Scheduler started. Press Ctrl+C to stop.")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("Scheduler stopped.")


if __name__ == "__main__":
    main()
