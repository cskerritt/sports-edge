"""
Management command: seed_initial_data

First-time data population for a fresh deployment.
Runs full ingest for all sports (teams, schedules, stats) then
builds Elo ratings, predictions, and market data.

Usage:
    python manage.py seed_initial_data                 # all sports, current year
    python manage.py seed_initial_data --season 2024   # specific season
    python manage.py seed_initial_data --sport NBA     # single sport
"""

import logging
import time
from datetime import date

from django.core.management import call_command
from django.core.management.base import BaseCommand

logger = logging.getLogger("seed_initial_data")


class Command(BaseCommand):
    help = "Seed a fresh database with teams, schedules, Elo ratings, predictions, and market data"

    def add_arguments(self, parser):
        parser.add_argument(
            "--sport",
            type=str,
            default=None,
            help="Only seed this sport (NFL, NBA, NHL, MLB, SOCCER)",
        )
        parser.add_argument(
            "--season",
            type=int,
            default=None,
            help="Season year to ingest (defaults to current year)",
        )
        parser.add_argument(
            "--skip-markets",
            action="store_true",
            help="Skip Kalshi market fetch",
        )

    def handle(self, *args, **options):
        sport_filter = options["sport"]
        season = options["season"] or date.today().year
        skip_markets = options["skip_markets"]

        start = time.time()

        self.stdout.write(self.style.MIGRATE_HEADING("=" * 60))
        self.stdout.write(self.style.MIGRATE_HEADING("  SportsEdge Initial Data Seed"))
        self.stdout.write(self.style.MIGRATE_HEADING("=" * 60))
        self.stdout.write("")

        # Most sports seasons span two calendar years (e.g. NFL 2025 season
        # plays into early 2026).  Try the current year first, then previous.
        seasons_to_try = [season]
        if season == date.today().year:
            seasons_to_try.append(season - 1)

        # ------------------------------------------------------------------
        # Step 1: Full ingest (teams + schedules + scores + injuries + stats)
        # ------------------------------------------------------------------
        self.stdout.write(self.style.HTTP_INFO(">>> 1. Full data ingest (teams, schedules, scores, injuries)..."))
        for s in seasons_to_try:
            self.stdout.write(f"  Trying season {s}...")
            try:
                ingest_args = ["--season", str(s)]
                if sport_filter:
                    ingest_args += ["--sport", sport_filter]
                call_command("ingest_all", *ingest_args, stdout=self.stdout, stderr=self.stderr)
            except SystemExit:
                self.stdout.write(self.style.WARNING(f"  Ingest for season {s} completed with some errors (continuing)"))
            except Exception as exc:
                self.stderr.write(self.style.ERROR(f"  Ingest for season {s} failed: {exc}"))
                logger.exception("seed_initial_data: ingest_all failed for season %s", s)

        # ------------------------------------------------------------------
        # Step 2: Build Elo ratings from historical results
        # ------------------------------------------------------------------
        self.stdout.write("")
        self.stdout.write(self.style.HTTP_INFO(">>> 2. Building Elo ratings from game results..."))
        try:
            elo_args = ["--reset"]
            if sport_filter:
                elo_args += ["--sport", sport_filter]
            call_command("update_elo", *elo_args, stdout=self.stdout, stderr=self.stderr)
        except Exception as exc:
            self.stderr.write(self.style.ERROR(f"  Elo build failed: {exc}"))
            logger.exception("seed_initial_data: update_elo failed")

        # ------------------------------------------------------------------
        # Step 3: Generate predictions for upcoming games
        # ------------------------------------------------------------------
        self.stdout.write("")
        self.stdout.write(self.style.HTTP_INFO(">>> 3. Generating predictions for upcoming games..."))
        try:
            pred_args = ["--days-ahead", "14", "--force"]
            if sport_filter:
                pred_args += ["--sport", sport_filter]
            call_command("run_predictions", *pred_args, stdout=self.stdout, stderr=self.stderr)
        except Exception as exc:
            self.stderr.write(self.style.ERROR(f"  Predictions failed: {exc}"))
            logger.exception("seed_initial_data: run_predictions failed")

        # ------------------------------------------------------------------
        # Step 4: Fetch Kalshi markets and calculate edges
        # ------------------------------------------------------------------
        if not skip_markets:
            self.stdout.write("")
            self.stdout.write(self.style.HTTP_INFO(">>> 4. Discovering Kalshi markets and calculating edges..."))
            try:
                call_command(
                    "fetch_kalshi_markets",
                    "--discover",
                    "--edges",
                    stdout=self.stdout,
                    stderr=self.stderr,
                )
            except Exception as exc:
                self.stderr.write(self.style.ERROR(f"  Market fetch failed: {exc}"))
                logger.exception("seed_initial_data: fetch_kalshi_markets failed")

        # ------------------------------------------------------------------
        # Done
        # ------------------------------------------------------------------
        elapsed = time.time() - start
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("=" * 60))
        self.stdout.write(self.style.SUCCESS(f"  Seed complete in {elapsed:.1f}s"))
        self.stdout.write(self.style.MIGRATE_HEADING("=" * 60))
