"""Force-refresh today's games from ESPN for all sports."""
import logging

from django.core.management.base import BaseCommand
from django.utils import timezone

from sports.management.commands.ingest_all import _SPORT_INGESTORS, _load_ingestor

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Force-refresh today's games from ESPN scoreboard for all sports."

    def add_arguments(self, parser):
        parser.add_argument(
            "--sport",
            type=str,
            help="Only refresh a specific sport (e.g. NBA, NFL, NCAAM, MMA)",
        )

    def handle(self, *args, **options):
        today = timezone.localdate()
        sport_filter = (options.get("sport") or "").upper()

        for sport, dotted_path in _SPORT_INGESTORS.items():
            if sport_filter and sport != sport_filter:
                continue

            self.stdout.write(f"Refreshing {sport} for {today}...")
            try:
                IngestorClass = _load_ingestor(dotted_path)
                ingestor = IngestorClass()
                ingestor.ingest_teams()
                result = ingestor.ingest_scores(game_date=today)
                self.stdout.write(
                    self.style.SUCCESS(
                        f"  {sport}: created={result['created']} "
                        f"updated={result['updated']} errors={result['errors']}"
                    )
                )
            except Exception as exc:
                self.stdout.write(self.style.ERROR(f"  {sport}: {exc}"))

        self.stdout.write(self.style.SUCCESS("Done."))
