import logging

from django.core.management.base import BaseCommand

from markets.edge_calculator import EdgeCalculator

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Calculate edges between model predictions and market prices"

    def add_arguments(self, parser):
        parser.add_argument(
            "--threshold",
            type=float,
            default=None,
            help=(
                "Minimum absolute edge to flag an alert "
                "(overrides settings.EDGE_ALERT_THRESHOLD)"
            ),
        )
        parser.add_argument(
            "--resolve",
            action="store_true",
            help="Also resolve open alerts for contracts that have been settled",
        )

    def handle(self, *args, **options):
        threshold = options["threshold"]
        should_resolve = options["resolve"]

        calculator = EdgeCalculator(edge_threshold=threshold)

        # --- Run edge detection ---
        self.stdout.write("Running edge calculator...")
        try:
            run_result = calculator.run_all()
        except Exception as exc:
            self.stderr.write(
                self.style.ERROR(f"EdgeCalculator.run_all() failed: {exc}")
            )
            logger.exception("calculate_edges: run_all unhandled exception")
            raise SystemExit(1)

        processed = run_result.get("processed", 0)
        alerts_created = run_result.get("alerts_created", 0)
        alerts_updated = run_result.get("alerts_updated", 0)
        no_edge = run_result.get("no_edge", 0)
        errors = run_result.get("errors", 0)

        if errors:
            self.stderr.write(
                self.style.WARNING(
                    f"Edge calculation complete with errors: "
                    f"processed={processed} created={alerts_created} "
                    f"updated={alerts_updated} no_edge={no_edge} errors={errors}"
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Edge calculation complete: "
                    f"processed={processed} created={alerts_created} "
                    f"updated={alerts_updated} no_edge={no_edge} errors={errors}"
                )
            )

        # --- Optional: resolve settled alerts ---
        if should_resolve:
            self.stdout.write("Resolving settled alerts...")
            try:
                resolve_result = calculator.resolve_alerts()
            except Exception as exc:
                self.stderr.write(
                    self.style.ERROR(f"EdgeCalculator.resolve_alerts() failed: {exc}")
                )
                logger.exception("calculate_edges: resolve_alerts unhandled exception")
            else:
                resolved = resolve_result.get("resolved", 0)
                hits = resolve_result.get("hits", 0)
                misses = resolve_result.get("misses", 0)
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Resolved {resolved} alerts: {hits} hits, {misses} misses"
                    )
                )

        # --- Print top 10 open edges ---
        self._print_top_edges(calculator)

    def _print_top_edges(self, calculator: EdgeCalculator):
        try:
            leaderboard = calculator.get_edge_leaderboard(limit=10)
        except Exception as exc:
            self.stderr.write(
                self.style.ERROR(f"Could not retrieve edge leaderboard: {exc}")
            )
            logger.exception("calculate_edges: get_edge_leaderboard unhandled exception")
            return

        if not leaderboard:
            self.stdout.write("  No open edge alerts found.")
            return

        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("=" * 80))
        self.stdout.write(self.style.MIGRATE_HEADING("  Top 10 Open Edges"))
        self.stdout.write(self.style.MIGRATE_HEADING("=" * 80))

        header = (
            f"  {'Sport':<8} {'Edge':>7} {'Model':>7} {'Market':>7} "
            f"{'Kelly%':>7} {'Conf':>6}  Contract"
        )
        self.stdout.write(header)
        self.stdout.write(self.style.MIGRATE_HEADING("-" * 80))

        for entry in leaderboard:
            edge = entry["edge"]
            direction = "YES" if edge > 0 else "NO "
            edge_str = f"{edge:+.3f}"
            model_str = f"{entry['model_prob']:.3f}"
            market_str = f"{entry['market_prob']:.3f}"
            kelly_str = f"{entry['kelly_fraction'] * 100:.2f}"
            conf_str = f"{entry['confidence']:.2f}"
            title = entry["contract_title"][:36]

            row = (
                f"  {entry['sport']:<8} {edge_str:>7} {model_str:>7} "
                f"{market_str:>7} {kelly_str:>7} {conf_str:>6}  "
                f"[{direction}] {title}"
            )
            # Colour positive edges green, negative red
            if edge > 0:
                self.stdout.write(self.style.SUCCESS(row))
            else:
                self.stdout.write(self.style.ERROR(row))

        self.stdout.write(self.style.MIGRATE_HEADING("=" * 80))
        self.stdout.write("")
