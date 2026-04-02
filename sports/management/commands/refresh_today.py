"""Force-refresh today's games from ESPN for all sports."""
import logging
from django.core.management.base import BaseCommand
from django.utils import timezone

from sports.ingestion.nba import NBAIngestor
from sports.ingestion.nfl import NFLIngestor
from sports.ingestion.nhl import NHLIngestor
from sports.ingestion.mlb import MLBIngestor

logger = logging.getLogger(__name__)

INGESTORS = [
    NBAIngestor,
    NFLIngestor,
    NHLIngestor,
    MLBIngestor,
]


class Command(BaseCommand):
    help = "Force-refresh today's games from ESPN scoreboard for all sports."

    def add_arguments(self, parser):
        parser.add_argument(
            "--sport",
            type=str,
            help="Only refresh a specific sport (NBA, NFL, NHL, MLB)",
        )

    def handle(self, *args, **options):
        today = timezone.localdate()
        sport_filter = (options.get("sport") or "").upper()

        for ingestor_cls in INGESTORS:
            if sport_filter and ingestor_cls.sport != sport_filter:
                continue

            self.stdout.write(f"Refreshing {ingestor_cls.sport} for {today}...")
            try:
                ingestor = ingestor_cls()
                ingestor.ingest_teams()
                result = ingestor.ingest_scores(game_date=today)
                self.stdout.write(
                    self.style.SUCCESS(
                        f"  {ingestor_cls.sport}: created={result['created']} "
                        f"updated={result['updated']} errors={result['errors']}"
                    )
                )
            except Exception as exc:
                self.stdout.write(self.style.ERROR(f"  {ingestor_cls.sport}: {exc}"))

        self.stdout.write(self.style.SUCCESS("Done."))
