import logging
from datetime import date

from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)

_SPORT_INGESTORS = {
    "NFL": "sports.ingestion.nfl.NFLIngestor",
    "NBA": "sports.ingestion.nba.NBAIngestor",
    "NHL": "sports.ingestion.nhl.NHLIngestor",
    "MLB": "sports.ingestion.mlb.MLBIngestor",
    "SOCCER": "sports.ingestion.soccer.SoccerIngestor",
}


def _load_ingestor(dotted_path: str):
    """Import and return an ingestor class from a dotted module path."""
    module_path, class_name = dotted_path.rsplit(".", 1)
    import importlib

    module = importlib.import_module(module_path)
    return getattr(module, class_name)


class Command(BaseCommand):
    help = "Run full ingest for all sports"

    def add_arguments(self, parser):
        parser.add_argument(
            "--season",
            type=int,
            default=None,
            help="Season year (e.g. 2024); defaults to current year",
        )
        parser.add_argument(
            "--scores-only",
            action="store_true",
            help="Only ingest today's scores for all sports",
        )
        parser.add_argument(
            "--sport",
            type=str,
            default=None,
            choices=list(_SPORT_INGESTORS.keys()),
            help="Ingest only this sport",
        )

    def handle(self, *args, **options):
        season = options["season"] or date.today().year
        scores_only = options["scores_only"]
        sport_filter = options["sport"]

        sports_to_run = (
            [sport_filter] if sport_filter else list(_SPORT_INGESTORS.keys())
        )

        summary = {}

        for sport in sports_to_run:
            dotted = _SPORT_INGESTORS[sport]
            self.stdout.write(f"  [{sport}] Starting ingest...")

            try:
                IngestorClass = _load_ingestor(dotted)
                ingestor = IngestorClass()

                if scores_only:
                    # Always ingest teams first (needed for ESPN ID backfill)
                    ingestor.ingest_teams()
                    result = ingestor.ingest_scores()
                else:
                    result = ingestor.run_full_ingest(season)

                summary[sport] = {"status": "OK", "result": result}
                self.stdout.write(self.style.SUCCESS(f"  [{sport}] Done: {result}"))

            except Exception as exc:
                logger.exception("[%s] Ingest failed", sport)
                summary[sport] = {"status": "ERROR", "error": str(exc)}
                self.stderr.write(
                    self.style.ERROR(f"  [{sport}] FAILED: {exc}")
                )

        # Summary table
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("=" * 60))
        self.stdout.write(self.style.MIGRATE_HEADING("  Ingest Summary"))
        self.stdout.write(self.style.MIGRATE_HEADING("=" * 60))

        ok_sports = [s for s, v in summary.items() if v["status"] == "OK"]
        err_sports = [s for s, v in summary.items() if v["status"] == "ERROR"]

        for sport in ok_sports:
            result = summary[sport]["result"]
            if isinstance(result, dict):
                # Aggregate created/updated/errors across nested op dicts
                total_created = sum(
                    (v.get("created", 0) if isinstance(v, dict) else 0)
                    for v in result.values()
                )
                total_updated = sum(
                    (v.get("updated", 0) if isinstance(v, dict) else 0)
                    for v in result.values()
                )
                total_errors = sum(
                    (v.get("errors", 0) if isinstance(v, dict) else 0)
                    for v in result.values()
                )
                self.stdout.write(
                    self.style.SUCCESS(
                        f"  {sport:<8} OK  "
                        f"created={total_created}  updated={total_updated}  errors={total_errors}"
                    )
                )
            else:
                self.stdout.write(self.style.SUCCESS(f"  {sport:<8} OK  {result}"))

        for sport in err_sports:
            self.stdout.write(
                self.style.ERROR(
                    f"  {sport:<8} ERROR  {summary[sport]['error']}"
                )
            )

        self.stdout.write(self.style.MIGRATE_HEADING("=" * 60))

        if err_sports:
            raise SystemExit(1)
