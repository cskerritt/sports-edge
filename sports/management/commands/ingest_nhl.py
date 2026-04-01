import logging
from datetime import date

from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Ingest NHL teams, schedule, scores, and injuries"

    def add_arguments(self, parser):
        parser.add_argument(
            "--season",
            type=int,
            default=None,
            help="Season year (e.g. 2024)",
        )
        parser.add_argument(
            "--teams-only",
            action="store_true",
            help="Only ingest teams",
        )
        parser.add_argument(
            "--scores-only",
            action="store_true",
            help="Only ingest scores",
        )
        parser.add_argument(
            "--injuries-only",
            action="store_true",
            help="Only ingest injuries",
        )
        parser.add_argument(
            "--date",
            type=str,
            default=None,
            help="Date for scores (YYYY-MM-DD)",
        )

    def handle(self, *args, **options):
        from sports.ingestion.nhl import NHLIngestor

        ingestor = NHLIngestor()
        season = options["season"] or date.today().year

        try:
            if options["teams_only"]:
                result = ingestor.ingest_teams()
            elif options["scores_only"]:
                game_date = (
                    date.fromisoformat(options["date"]) if options["date"] else None
                )
                result = ingestor.ingest_scores(game_date)
            elif options["injuries_only"]:
                result = ingestor.ingest_injuries()
            else:
                result = ingestor.run_full_ingest(season)
        except Exception as exc:
            self.stderr.write(self.style.ERROR(f"NHL ingest failed: {exc}"))
            logger.exception("NHL ingest unhandled exception")
            raise SystemExit(1)

        self.stdout.write(self.style.SUCCESS(f"NHL ingest complete: {result}"))
