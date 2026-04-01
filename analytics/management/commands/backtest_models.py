import logging
import math

from django.core.management.base import BaseCommand

from analytics.models import BacktestResult, GamePrediction
from sports.models import Game, GameStatus, Sport

logger = logging.getLogger(__name__)

# Payout multiplier for a flat $1 bet at American -110 odds
_FLAT_BET_PAYOUT = 100 / 110  # ≈ 0.9091


class Command(BaseCommand):
    help = "Compute backtest accuracy for all sports and model versions"

    def add_arguments(self, parser):
        parser.add_argument(
            "--sport",
            type=str,
            default=None,
            choices=[s[0] for s in Sport.choices],
            help="Only backtest this sport",
        )
        parser.add_argument(
            "--season",
            type=int,
            default=None,
            help="Only include games from this season year",
        )
        parser.add_argument(
            "--model-version",
            type=str,
            default="ensemble_v1",
            help="Model version to evaluate",
        )

    def handle(self, *args, **options):
        sport_filter = options["sport"]
        season_filter = options["season"]
        model_version = options["model_version"]

        sports_to_process = (
            [sport_filter] if sport_filter else [s[0] for s in Sport.choices]
        )

        results_table = []

        for sport in sports_to_process:
            self.stdout.write(f"Backtesting {sport} ({model_version})...")
            try:
                metrics = self._backtest_sport(sport, season_filter, model_version)
                results_table.append((sport, metrics))
            except Exception as exc:
                self.stderr.write(
                    self.style.ERROR(f"  [{sport}] Backtest failed: {exc}")
                )
                logger.exception("[%s] backtest_models unhandled exception", sport)

        # Print summary table
        self._print_table(results_table, model_version)

    def _backtest_sport(self, sport: str, season_year, model_version: str) -> dict:
        from sports.models import Season

        predictions_qs = (
            GamePrediction.objects.filter(
                model_version=model_version,
                game__sport=sport,
                game__status=GameStatus.FINAL,
            )
            .select_related("game", "game__season")
        )

        if season_year is not None:
            predictions_qs = predictions_qs.filter(
                game__season__year=season_year
            )

        predictions = list(predictions_qs)

        if not predictions:
            self.stdout.write(
                f"  [{sport}] No completed predictions found — skipping."
            )
            return {}

        total_games = len(predictions)
        correct = 0
        brier_sum = 0.0
        log_loss_sum = 0.0
        roi_sum = 0.0

        correct_totals = 0
        total_total_games = 0

        for pred in predictions:
            game = pred.game
            if game.home_score is None or game.away_score is None:
                continue

            home_won = game.home_score > game.away_score
            outcome = 1.0 if home_won else 0.0
            prob = pred.home_win_prob

            # Clamp to avoid log(0)
            prob_clamped = max(1e-7, min(prob, 1 - 1e-7))

            # Accuracy
            model_pick_home = prob >= 0.5
            if model_pick_home == home_won:
                correct += 1

            # Brier score contribution
            brier_sum += (prob - outcome) ** 2

            # Log loss contribution
            log_loss_sum += -(
                outcome * math.log(prob_clamped)
                + (1 - outcome) * math.log(1 - prob_clamped)
            )

            # Flat-bet ROI simulation at -110
            if model_pick_home:
                roi_sum += _FLAT_BET_PAYOUT if home_won else -1.0
            else:
                roi_sum += _FLAT_BET_PAYOUT if not home_won else -1.0

            # Over/under accuracy
            if pred.predicted_total is not None and game.total_score is not None:
                total_total_games += 1
                # Model predicted over if predicted > actual (no line available here)
                # Use a rough proxy: predicted total vs actual total direction
                pred_over = pred.predicted_total > game.total_score
                # We can't really evaluate over/under without a line, so skip accuracy
                # This is a placeholder — real accuracy needs the bookmaker line
                correct_totals += 0  # Not counted without a line

        accuracy = correct / total_games if total_games else None
        brier_score = brier_sum / total_games if total_games else None
        log_loss = log_loss_sum / total_games if total_games else None
        roi = (roi_sum / total_games) * 100 if total_games else None
        avg_edge = None  # Would require market prices; left for EdgeCalculator

        totals_accuracy = (
            correct_totals / total_total_games if total_total_games else None
        )

        # Resolve season FK
        season_obj = None
        if season_year is not None:
            from sports.models import Season

            season_obj = Season.objects.filter(
                sport=sport, year=season_year
            ).first()

        # Persist result
        BacktestResult.objects.update_or_create(
            sport=sport,
            model_version=model_version,
            season=season_obj,
            defaults={
                "total_games": total_games,
                "correct_predictions": correct,
                "accuracy": accuracy,
                "brier_score": brier_score,
                "log_loss": log_loss,
                "roi": roi,
                "avg_edge": avg_edge,
                "total_total_games": total_total_games,
                "correct_totals": correct_totals,
                "totals_accuracy": totals_accuracy,
            },
        )

        return {
            "total_games": total_games,
            "correct": correct,
            "accuracy": accuracy,
            "brier_score": brier_score,
            "log_loss": log_loss,
            "roi": roi,
            "total_total_games": total_total_games,
            "totals_accuracy": totals_accuracy,
        }

    def _print_table(self, results_table: list, model_version: str):
        if not results_table:
            return

        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("=" * 80))
        self.stdout.write(
            self.style.MIGRATE_HEADING(f"  Backtest Results — {model_version}")
        )
        self.stdout.write(self.style.MIGRATE_HEADING("=" * 80))

        header = (
            f"  {'Sport':<8} {'Games':>6} {'Correct':>8} {'Accuracy':>10} "
            f"{'Brier':>8} {'LogLoss':>9} {'ROI%':>8}"
        )
        self.stdout.write(header)
        self.stdout.write(self.style.MIGRATE_HEADING("-" * 80))

        for sport, metrics in results_table:
            if not metrics:
                self.stdout.write(f"  {sport:<8} (no data)")
                continue

            accuracy_str = (
                f"{metrics['accuracy']:.1%}" if metrics.get("accuracy") is not None else "N/A"
            )
            brier_str = (
                f"{metrics['brier_score']:.4f}" if metrics.get("brier_score") is not None else "N/A"
            )
            log_str = (
                f"{metrics['log_loss']:.4f}" if metrics.get("log_loss") is not None else "N/A"
            )
            roi_str = (
                f"{metrics['roi']:+.2f}" if metrics.get("roi") is not None else "N/A"
            )

            row = (
                f"  {sport:<8} {metrics.get('total_games', 0):>6} "
                f"{metrics.get('correct', 0):>8} {accuracy_str:>10} "
                f"{brier_str:>8} {log_str:>9} {roi_str:>8}"
            )
            self.stdout.write(row)

        self.stdout.write(self.style.MIGRATE_HEADING("=" * 80))
        self.stdout.write("")
