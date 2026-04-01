import logging

from django.core.management.base import BaseCommand

from markets.coinbase import CoinbasePredictionClient
from markets.models import ContractType, MarketContract

logger = logging.getLogger(__name__)

# Keywords that map product display names to sport codes
_SPORT_KEYWORDS: dict[str, list[str]] = {
    "NFL": ["NFL", "SUPER BOWL", "SUPER-BOWL", "AFC", "NFC", "CHIEFS", "EAGLES",
            "COWBOYS", "PATRIOTS", "RAVENS", "BILLS", "NINERS", "PACKERS"],
    "NBA": ["NBA", "NBA FINALS", "CELTICS", "LAKERS", "WARRIORS", "BUCKS", "HEAT",
            "SUNS", "NUGGETS", "KNICKS"],
    "NHL": ["NHL", "STANLEY CUP", "BRUINS", "RANGERS", "LEAFS", "PENGUINS",
            "LIGHTNING", "AVALANCHE", "OILERS", "GOLDEN KNIGHTS"],
    "MLB": ["MLB", "WORLD SERIES", "YANKEES", "DODGERS", "RED SOX", "CUBS",
            "BRAVES", "ASTROS", "PADRES", "METS"],
    "SOCCER": ["PREMIER LEAGUE", "LA LIGA", "CHAMPIONS LEAGUE", "FIFA", "UEFA",
               "WORLD CUP", "MLS", "EPL", "BUNDESLIGA", "SERIE A"],
}


def _infer_sport(display_name: str) -> str:
    """Return a Sport code inferred from the product's display name, or empty string."""
    dn_upper = display_name.upper()
    for sport, keywords in _SPORT_KEYWORDS.items():
        if any(kw in dn_upper for kw in keywords):
            return sport
    return ""


class Command(BaseCommand):
    help = "Fetch latest Coinbase prediction market prices"

    def add_arguments(self, parser):
        parser.add_argument(
            "--discover",
            action="store_true",
            help=(
                "Also search for new contracts to add by calling "
                "get_prediction_products() and creating MarketContract records "
                "for any contract matching sports keywords"
            ),
        )

    def handle(self, *args, **options):
        client = CoinbasePredictionClient()

        if options["discover"]:
            self._discover_contracts(client)

        self.stdout.write("Fetching current market prices...")
        try:
            price_result = client.fetch_and_store_prices()
        except Exception as exc:
            self.stderr.write(
                self.style.ERROR(f"fetch_and_store_prices failed: {exc}")
            )
            logger.exception("fetch_markets: fetch_and_store_prices unhandled exception")
            raise SystemExit(1)

        fetched = price_result.get("fetched", 0)
        errors = price_result.get("errors", 0)

        if errors:
            self.stderr.write(
                self.style.WARNING(
                    f"Price fetch complete with errors: fetched={fetched} errors={errors}"
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Price fetch complete: fetched={fetched} errors={errors}"
                )
            )

    def _discover_contracts(self, client: CoinbasePredictionClient):
        self.stdout.write("Discovering new prediction market contracts...")
        try:
            products = client.get_prediction_products()
        except Exception as exc:
            self.stderr.write(
                self.style.ERROR(f"get_prediction_products failed: {exc}")
            )
            logger.exception("fetch_markets: discover phase unhandled exception")
            return

        if not products:
            self.stdout.write("  No products returned from Coinbase (check credentials).")
            return

        new_count = 0
        skipped_count = 0

        for product in products:
            product_id: str = product.get("product_id", "")
            display_name: str = product.get("display_name", product_id)

            if not product_id:
                continue

            sport = _infer_sport(display_name)
            if not sport:
                # Cannot map to a known sport — skip
                skipped_count += 1
                continue

            # Infer contract type from product_id / display name
            pid_upper = product_id.upper()
            if "-YES-" in pid_upper:
                contract_type = ContractType.HOME_WIN  # HOME_WIN as default YES side
            elif "OVER" in pid_upper:
                contract_type = ContractType.OVER
            elif "UNDER" in pid_upper:
                contract_type = ContractType.UNDER
            else:
                contract_type = ContractType.OTHER

            _, created = MarketContract.objects.get_or_create(
                coinbase_product_id=product_id,
                defaults={
                    "sport": sport,
                    "title": display_name,
                    "contract_type": contract_type,
                    "is_active": True,
                    "is_resolved": False,
                },
            )

            if created:
                new_count += 1
                logger.info("Discovered new contract: %s (%s)", product_id, sport)

        self.stdout.write(
            self.style.SUCCESS(
                f"  Discovery complete: {new_count} new contracts created, "
                f"{skipped_count} products skipped (no sport match)."
            )
        )
