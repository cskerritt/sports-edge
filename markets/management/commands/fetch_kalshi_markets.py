"""
Management command: fetch_kalshi_markets

Usage:
    python manage.py fetch_kalshi_markets              # refresh prices only
    python manage.py fetch_kalshi_markets --discover   # discover + refresh prices
    python manage.py fetch_kalshi_markets --edges      # discover + refresh + calculate edges
"""

import logging

from django.core.management.base import BaseCommand

from markets.kalshi import KalshiPredictionClient

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Fetch Kalshi prediction market data and optionally run edge calculations"

    def add_arguments(self, parser):
        parser.add_argument(
            "--discover",
            action="store_true",
            help="Discover new Kalshi sports contracts and create MarketContract records",
        )
        parser.add_argument(
            "--edges",
            action="store_true",
            help="After fetching, run calculate_edges to update EdgeAlert records",
        )

    def handle(self, *args, **options):
        client = KalshiPredictionClient()

        if options["discover"]:
            self.stdout.write("Discovering Kalshi sports contracts...")
            result = client.discover_and_create_contracts()
            created = result["created"]
            updated = result["updated"]
            skipped = result["skipped"]
            self.stdout.write(
                self.style.SUCCESS(
                    f"  Discovery complete: {created} new, {updated} updated, {skipped} skipped"
                )
            )
        else:
            self.stdout.write("Refreshing prices for existing Kalshi contracts...")
            result = client.fetch_and_store_prices()
            fetched = result["fetched"]
            errors = result["errors"]
            msg = f"  Price refresh: fetched={fetched} errors={errors}"
            if errors:
                self.stdout.write(self.style.WARNING(msg))
            else:
                self.stdout.write(self.style.SUCCESS(msg))

        if options["edges"]:
            self.stdout.write("Running edge calculations...")
            from markets.edge_calculator import EdgeCalculator
            calc = EdgeCalculator()
            edge_result = calc.run_all()
            self.stdout.write(
                self.style.SUCCESS(
                    f"  Edges: {edge_result.get('alerts_created', 0)} created, "
                    f"{edge_result.get('alerts_updated', 0)} updated, "
                    f"{edge_result.get('errors', 0)} errors"
                )
            )

            # Print top edges if any
            leaderboard = calc.get_edge_leaderboard(limit=5)
            if leaderboard:
                self.stdout.write("\nTop 5 edges:")
                for alert in leaderboard:
                    edge = alert["edge"]
                    direction = "YES" if edge > 0 else "NO"
                    self.stdout.write(
                        f"  [{alert['sport']}] {alert['contract_title'][:60]} "
                        f"→ {direction} {abs(edge):.1%} edge "
                        f"(model {alert['model_prob']:.0%} vs mkt {alert['market_prob']:.0%})"
                    )
