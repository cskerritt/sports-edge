import logging
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from analytics.adjustments import compute_total_adjustment, travel_distance_km
from analytics.elo import DEFAULT_ELO, EloEngine
from analytics.models import EloRating, GamePrediction
from analytics.over_under import TotalModel, expected_total_from_elo
from analytics.win_probability import (
    WinProbabilityModel,
    blend_predictions,
    calibrate_probability,
)
from sports.models import Game, GameStatus, Sport

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Generate model predictions for upcoming games"

    def add_arguments(self, parser):
        parser.add_argument(
            "--sport",
            type=str,
            default=None,
            choices=[s[0] for s in Sport.choices],
            help="Only predict games for this sport",
        )
        parser.add_argument(
            "--days-ahead",
            type=int,
            default=7,
            help="Predict games within N days from today (default: 7)",
        )
        parser.add_argument(
            "--model-version",
            type=str,
            default="ensemble_v1",
            help="Model version tag to store on the prediction record",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Overwrite existing predictions for the same game + model version",
        )

    def handle(self, *args, **options):
        sport_filter = options["sport"]
        days_ahead = options["days_ahead"]
        model_version = options["model_version"]
        force = options["force"]

        today = timezone.localdate()
        cutoff = today + timedelta(days=days_ahead)

        sports_to_process = (
            [sport_filter] if sport_filter else [s[0] for s in Sport.choices]
        )

        total_created = 0
        total_updated = 0
        total_skipped = 0
        total_errors = 0

        for sport in sports_to_process:
            self.stdout.write(f"Predicting {sport} games...")
            try:
                c, u, s, e = self._predict_sport(
                    sport, today, cutoff, model_version, force
                )
                total_created += c
                total_updated += u
                total_skipped += s
                total_errors += e
                self.stdout.write(
                    self.style.SUCCESS(
                        f"  [{sport}] created={c} updated={u} skipped={s} errors={e}"
                    )
                )
            except Exception as exc:
                self.stderr.write(
                    self.style.ERROR(f"  [{sport}] prediction run failed: {exc}")
                )
                logger.exception("[%s] run_predictions unhandled exception", sport)
                total_errors += 1

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                f"Predictions complete — "
                f"created={total_created} updated={total_updated} "
                f"skipped={total_skipped} errors={total_errors}"
            )
        )

    def _predict_sport(
        self,
        sport: str,
        today,
        cutoff,
        model_version: str,
        force: bool,
    ) -> tuple[int, int, int, int]:
        elo_engine = EloEngine(sport)
        wp_model = WinProbabilityModel(sport)
        total_model = TotalModel(sport)

        # Fetch latest Elo ratings keyed by team_id
        # Use subquery for SQLite compatibility (no DISTINCT ON)
        from django.db.models import Max
        latest_elo_ids = (
            EloRating.objects.filter(team__sport=sport)
            .values("team_id")
            .annotate(latest_id=Max("id"))
            .values_list("latest_id", flat=True)
        )
        elo_map: dict[int, float] = {}
        for elo_rec in EloRating.objects.filter(id__in=latest_elo_ids).select_related("team"):
            elo_map[elo_rec.team_id] = elo_rec.rating

        upcoming_games = (
            Game.objects.filter(
                sport=sport,
                status__in=[GameStatus.SCHEDULED, GameStatus.IN_PROGRESS],
                game_date__gte=today,
                game_date__lte=cutoff,
            )
            .select_related(
                "home_team", "away_team", "season"
            )
        )

        created = 0
        updated = 0
        skipped = 0
        errors = 0

        for game in upcoming_games:
            try:
                home_elo = elo_map.get(game.home_team_id, DEFAULT_ELO)
                away_elo = elo_map.get(game.away_team_id, DEFAULT_ELO)

                # --- Elo win probability ---
                elo_home_prob = elo_engine.win_probability(home_elo, away_elo)

                # --- Rest / travel adjustments ---
                home_rest = game.home_rest_days if game.home_rest_days is not None else 3
                away_rest = game.away_rest_days if game.away_rest_days is not None else 3

                home_tz = game.home_team.venue_timezone or "America/New_York"
                away_tz = game.away_team.venue_timezone or "America/New_York"
                dist_km = travel_distance_km(away_tz, home_tz)

                adjustments = compute_total_adjustment(
                    home_rest=home_rest,
                    away_rest=away_rest,
                    home_travel_km=0.0,
                    away_travel_km=dist_km,
                    sport=sport,
                )

                # --- Logistic model win probability ---
                logistic_home_prob = wp_model.predict_from_context(
                    home_elo=home_elo,
                    away_elo=away_elo,
                    home_rest_days=float(home_rest),
                    away_rest_days=float(away_rest),
                    home_is_home=not game.neutral_site,
                    travel_distance_km=dist_km,
                )
                logistic_home_prob = calibrate_probability(logistic_home_prob, sport)

                # --- Ensemble blend: 60% Elo, 40% logistic ---
                ensemble_home_prob = blend_predictions(
                    elo_prob=elo_home_prob,
                    logistic_prob=logistic_home_prob,
                    weights=(0.6, 0.4),
                )
                ensemble_away_prob = round(1.0 - ensemble_home_prob, 6)

                # --- Predicted total ---
                predicted_total = expected_total_from_elo(
                    home_elo=home_elo,
                    away_elo=away_elo,
                    league_avg_total=total_model.league_avg,
                    sport=sport,
                )

                # --- Predicted spread (positive = home favoured) ---
                # Simple linear mapping: every 1% home win prob above 50% ≈ 0.3 pts
                spread_pts = (ensemble_home_prob - 0.5) * 30.0

                # --- Confidence (mean distance from 50/50) ---
                confidence = abs(ensemble_home_prob - 0.5) * 2.0

                adjustments_applied = {
                    "elo_home": round(home_elo, 2),
                    "elo_away": round(away_elo, 2),
                    "home_rest": home_rest,
                    "away_rest": away_rest,
                    "away_travel_km": round(dist_km, 1),
                    "home_adjustment": adjustments["home_adjustment"],
                    "away_adjustment": adjustments["away_adjustment"],
                    "adjustment_notes": adjustments["notes"],
                }

                defaults = {
                    "home_win_prob": round(ensemble_home_prob, 6),
                    "away_win_prob": round(ensemble_away_prob, 6),
                    "draw_prob": 0.0,
                    "predicted_spread": round(spread_pts, 2),
                    "predicted_total": round(predicted_total, 2),
                    "elo_home_win_prob": round(elo_home_prob, 6),
                    "logistic_home_win_prob": round(logistic_home_prob, 6),
                    "confidence": round(confidence, 4),
                    "adjustments_applied": adjustments_applied,
                }

                if not force and GamePrediction.objects.filter(
                    game=game, model_version=model_version
                ).exists():
                    skipped += 1
                    continue

                _, was_created = GamePrediction.objects.update_or_create(
                    game=game,
                    model_version=model_version,
                    defaults=defaults,
                )

                if was_created:
                    created += 1
                else:
                    updated += 1

            except Exception as exc:
                logger.exception(
                    "[%s] Error predicting game %s: %s", sport, game, exc
                )
                self.stderr.write(
                    self.style.ERROR(f"    Error on game {game}: {exc}")
                )
                errors += 1

        return created, updated, skipped, errors
