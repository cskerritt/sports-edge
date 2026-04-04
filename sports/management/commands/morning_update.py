"""
Management command: morning_update

Runs the complete daily data pipeline:
1. Ingest scores for all sports (update yesterday/today results)
2. Ingest injuries for all sports
3. Update Elo ratings from new results
4. Run predictions for upcoming games
5. Fetch & discover Kalshi market contracts
6. Calculate edges between model predictions and market prices

Usage:
    python manage.py morning_update                    # full pipeline
    python manage.py morning_update --sport NFL        # single sport only
    python manage.py morning_update --skip-markets     # skip Kalshi fetch
    python manage.py morning_update --full-ingest      # also re-ingest teams & schedules
"""

import logging
import time

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.utils import timezone

logger = logging.getLogger("morning_update")


class Command(BaseCommand):
    help = "Run the complete daily data pipeline: scores, Elo, predictions, markets, edges"

    def add_arguments(self, parser):
        parser.add_argument(
            "--sport",
            type=str,
            default=None,
            help="Only process this sport (NFL, NBA, NHL, MLB, SOCCER)",
        )
        parser.add_argument(
            "--season",
            type=int,
            default=None,
            help="Season year (defaults to current year)",
        )
        parser.add_argument(
            "--skip-markets",
            action="store_true",
            help="Skip Kalshi market fetch and edge calculations",
        )
        parser.add_argument(
            "--full-ingest",
            action="store_true",
            help="Run full ingest (teams + schedules) instead of scores-only",
        )
        parser.add_argument(
            "--days-ahead",
            type=int,
            default=7,
            help="Predict games within N days from today (default: 7)",
        )

    def handle(self, *args, **options):
        sport_filter = options["sport"]
        season = options["season"] or timezone.localdate().year
        skip_markets = options["skip_markets"]
        full_ingest = options["full_ingest"]
        days_ahead = options["days_ahead"]

        start = time.time()
        errors = []

        self.stdout.write(self.style.MIGRATE_HEADING("=" * 60))
        self.stdout.write(self.style.MIGRATE_HEADING("  SportsEdge Morning Update"))
        self.stdout.write(self.style.MIGRATE_HEADING(f"  {timezone.localdate().isoformat()}"))
        self.stdout.write(self.style.MIGRATE_HEADING("=" * 60))
        self.stdout.write("")

        # ------------------------------------------------------------------
        # Step 1: Ingest sports data (teams, scores, injuries)
        # ------------------------------------------------------------------
        self._step("1. Ingesting sports data...")
        try:
            ingest_args = ["--season", str(season)]
            if sport_filter:
                ingest_args += ["--sport", sport_filter]
            if not full_ingest:
                ingest_args.append("--scores-only")
            call_command("ingest_all", *ingest_args, stdout=self.stdout, stderr=self.stderr)
        except SystemExit:
            # ingest_all raises SystemExit(1) on partial failures — that's OK
            errors.append("ingest_all had partial failures (see above)")
        except Exception as exc:
            errors.append(f"ingest_all: {exc}")
            self.stderr.write(self.style.ERROR(f"  Ingest failed: {exc}"))
            logger.exception("morning_update: ingest_all failed")

        # ------------------------------------------------------------------
        # Step 1b: Ingest team stats (needed for predictions)
        # ------------------------------------------------------------------
        self._step("1b. Ingesting team stats...")
        try:
            from sports.ingestion.nba import NBAIngestor

            sport_ingestors = [NBAIngestor]
            for cls in sport_ingestors:
                if sport_filter and cls.sport != sport_filter:
                    continue
                try:
                    ing = cls()
                    result = ing.ingest_team_stats(season)
                    self.stdout.write(
                        f"    {cls.sport} stats: created={result['created']} "
                        f"updated={result['updated']} errors={result['errors']}"
                    )
                except Exception as exc:
                    errors.append(f"team_stats_{cls.sport}: {exc}")
                    self.stderr.write(
                        self.style.ERROR(f"    {cls.sport} stats failed: {exc}")
                    )
        except Exception as exc:
            errors.append(f"team_stats: {exc}")
            logger.exception("morning_update: team_stats failed")

        # ------------------------------------------------------------------
        # Step 2: Update Elo ratings
        # ------------------------------------------------------------------
        self._step("2. Updating Elo ratings...")
        try:
            elo_args = []
            if sport_filter:
                elo_args += ["--sport", sport_filter]
            call_command("update_elo", *elo_args, stdout=self.stdout, stderr=self.stderr)
        except Exception as exc:
            errors.append(f"update_elo: {exc}")
            self.stderr.write(self.style.ERROR(f"  Elo update failed: {exc}"))
            logger.exception("morning_update: update_elo failed")

        # ------------------------------------------------------------------
        # Step 3: Run predictions
        # ------------------------------------------------------------------
        self._step("3. Running predictions for upcoming games...")
        try:
            pred_args = ["--days-ahead", str(days_ahead), "--force"]
            if sport_filter:
                pred_args += ["--sport", sport_filter]
            call_command("run_predictions", *pred_args, stdout=self.stdout, stderr=self.stderr)
        except Exception as exc:
            errors.append(f"run_predictions: {exc}")
            self.stderr.write(self.style.ERROR(f"  Predictions failed: {exc}"))
            logger.exception("morning_update: run_predictions failed")

        # ------------------------------------------------------------------
        # Step 4: Fetch Kalshi markets & calculate edges
        # ------------------------------------------------------------------
        if not skip_markets:
            self._step("4. Fetching Kalshi markets...")
            try:
                call_command(
                    "fetch_kalshi_markets",
                    "--discover",
                    "--edges",
                    stdout=self.stdout,
                    stderr=self.stderr,
                )
            except Exception as exc:
                errors.append(f"fetch_kalshi_markets: {exc}")
                self.stderr.write(self.style.ERROR(f"  Market fetch failed: {exc}"))
                logger.exception("morning_update: fetch_kalshi_markets failed")

            self._step("5. Calculating edges...")
            try:
                call_command(
                    "calculate_edges",
                    "--resolve",
                    stdout=self.stdout,
                    stderr=self.stderr,
                )
            except Exception as exc:
                errors.append(f"calculate_edges: {exc}")
                self.stderr.write(self.style.ERROR(f"  Edge calculation failed: {exc}"))
                logger.exception("morning_update: calculate_edges failed")

        # ------------------------------------------------------------------
        # Summary
        # ------------------------------------------------------------------
        elapsed = time.time() - start
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("=" * 60))
        self.stdout.write(self.style.MIGRATE_HEADING("  Morning Update Summary"))
        self.stdout.write(self.style.MIGRATE_HEADING("=" * 60))
        self.stdout.write(f"  Elapsed: {elapsed:.1f}s")

        if errors:
            self.stdout.write(self.style.WARNING(f"  Completed with {len(errors)} error(s):"))
            for err in errors:
                self.stdout.write(self.style.ERROR(f"    - {err}"))
        else:
            self.stdout.write(self.style.SUCCESS("  All steps completed successfully."))

        self.stdout.write(self.style.MIGRATE_HEADING("=" * 60))

    def _step(self, msg: str):
        self.stdout.write("")
        self.stdout.write(self.style.HTTP_INFO(f">>> {msg}"))
