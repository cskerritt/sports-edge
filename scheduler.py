#!/usr/bin/env python
"""
Standalone scheduler for SportsEdge.

Runs as a separate process alongside the web server.
Executes the morning_update pipeline on a configurable schedule.

Schedule (all times Eastern):
  - 06:00 AM: Full morning update (scores, Elo, predictions, markets, edges)
  - 12:00 PM: Midday scores + market refresh
  - 06:00 PM: Evening scores + market refresh
  - 10:00 PM: Late-night scores + Elo update for final games

Environment variables:
  MORNING_UPDATE_HOUR   — hour (0-23) for the main morning run (default: 6)
  ENABLE_SEED_ON_EMPTY  — if "true", run seed_initial_data when DB has no teams (default: true)
"""

import logging
import os
import sys
import time
from datetime import datetime

import django

# Bootstrap Django before importing anything else
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "sports_edge.settings.production")
django.setup()

from django.core.management import call_command  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("scheduler")


def db_is_empty() -> bool:
    """Return True if no teams exist in the database."""
    from sports.models import Team
    return Team.objects.count() == 0


def run_seed():
    """Run initial data seed if the database is empty."""
    logger.info("Database appears empty — running seed_initial_data...")
    try:
        call_command("seed_initial_data")
        logger.info("Seed completed successfully.")
    except Exception:
        logger.exception("seed_initial_data failed")


def run_morning_update(full_ingest: bool = False):
    """Run the daily morning update pipeline."""
    logger.info("Starting morning update (full_ingest=%s)...", full_ingest)
    try:
        args = []
        if full_ingest:
            args.append("--full-ingest")
        call_command("morning_update", *args)
        logger.info("Morning update completed.")
    except Exception:
        logger.exception("morning_update failed")


def run_scores_refresh():
    """Quick refresh: scores + market prices + edges."""
    logger.info("Starting scores refresh...")
    try:
        call_command("ingest_all", "--scores-only")
    except SystemExit:
        pass
    except Exception:
        logger.exception("ingest_all --scores-only failed")

    try:
        call_command("fetch_kalshi_markets")
    except Exception:
        logger.exception("fetch_kalshi_markets failed")

    try:
        call_command("calculate_edges")
    except Exception:
        logger.exception("calculate_edges failed")

    logger.info("Scores refresh completed.")


def run_late_night_update():
    """Late-night: scores + Elo update for games that just finished."""
    logger.info("Starting late-night update...")
    try:
        call_command("ingest_all", "--scores-only")
    except SystemExit:
        pass
    except Exception:
        logger.exception("ingest_all --scores-only failed")

    try:
        call_command("update_elo")
    except Exception:
        logger.exception("update_elo failed")

    try:
        call_command("calculate_edges", "--resolve")
    except Exception:
        logger.exception("calculate_edges --resolve failed")

    logger.info("Late-night update completed.")


def main():
    logger.info("SportsEdge scheduler starting...")

    # Seed on first run if database is empty
    enable_seed = os.environ.get("ENABLE_SEED_ON_EMPTY", "true").lower() == "true"
    if enable_seed and db_is_empty():
        run_seed()

    morning_hour = int(os.environ.get("MORNING_UPDATE_HOUR", "6"))

    # Schedule definition: (hour, minute, handler_name)
    schedule = [
        (morning_hour, 0, "morning", run_morning_update),
        (12, 0, "midday", run_scores_refresh),
        (18, 0, "evening", run_scores_refresh),
        (22, 0, "late_night", run_late_night_update),
    ]

    logger.info(
        "Schedule configured: morning=%02d:00, midday=12:00, evening=18:00, late_night=22:00",
        morning_hour,
    )

    # Track which jobs have run today so we don't double-fire
    last_run_date: dict[str, str] = {}

    while True:
        now = datetime.now()
        today_str = now.strftime("%Y-%m-%d")

        for hour, minute, name, handler in schedule:
            run_key = f"{name}:{today_str}"
            if run_key in last_run_date:
                continue
            if now.hour >= hour and now.minute >= minute:
                logger.info("Triggering scheduled job: %s", name)
                last_run_date[run_key] = today_str
                try:
                    handler()
                except Exception:
                    logger.exception("Scheduled job %s failed", name)

        # Sleep 60 seconds between checks
        time.sleep(60)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Scheduler stopped.")
        sys.exit(0)
