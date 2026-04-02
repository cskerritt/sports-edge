import logging

from django.core.management.base import BaseCommand
from django.utils import timezone

from analytics.elo import DEFAULT_ELO, EloEngine
from analytics.models import EloRating
from sports.models import Game, GameStatus, Sport

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Recompute Elo ratings from historical game results"

    def add_arguments(self, parser):
        parser.add_argument(
            "--sport",
            type=str,
            default=None,
            choices=[s[0] for s in Sport.choices],
            help="Only update Elo for this sport",
        )
        parser.add_argument(
            "--season",
            type=int,
            default=None,
            help="Only process games from this season year",
        )
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Delete all existing EloRating records first and start from 1500",
        )

    def handle(self, *args, **options):
        sport_filter = options["sport"]
        season_filter = options["season"]
        reset = options["reset"]

        sports_to_process = (
            [sport_filter] if sport_filter else [s[0] for s in Sport.choices]
        )

        for sport in sports_to_process:
            self.stdout.write(f"Processing Elo for {sport}...")
            try:
                self._process_sport(sport, season_filter, reset)
            except Exception as exc:
                self.stderr.write(
                    self.style.ERROR(f"[{sport}] Elo update failed: {exc}")
                )
                logger.exception("[%s] Elo update unhandled exception", sport)

    def _process_sport(self, sport: str, season_year, reset: bool):
        engine = EloEngine(sport)

        if reset:
            deleted_count, _ = EloRating.objects.filter(
                team__sport=sport
            ).delete()
            self.stdout.write(
                f"  [{sport}] Reset: deleted {deleted_count} EloRating records."
            )

        # Load all FINAL games ordered chronologically
        games_qs = (
            Game.objects.filter(sport=sport, status=GameStatus.FINAL)
            .select_related(
                "home_team", "away_team", "season"
            )
            .order_by("game_date", "game_time")
        )

        if season_year is not None:
            games_qs = games_qs.filter(season__year=season_year)

        games = list(games_qs)

        if not games:
            self.stdout.write(f"  [{sport}] No FINAL games found — skipping.")
            return

        # Current Elo state: team_id -> float
        current_ratings: dict[int, float] = {}

        # If not resetting, seed from latest stored EloRating records
        if not reset:
            from django.db.models import Max
            latest_elo_ids = (
                EloRating.objects.filter(team__sport=sport)
                .values("team_id")
                .annotate(latest_id=Max("id"))
                .values_list("latest_id", flat=True)
            )
            for elo_rec in EloRating.objects.filter(id__in=latest_elo_ids):
                current_ratings[elo_rec.team_id] = elo_rec.rating

        elo_records_to_create = []
        processed = 0
        skipped = 0

        for game in games:
            home_id = game.home_team_id
            away_id = game.away_team_id

            if game.home_score is None or game.away_score is None:
                skipped += 1
                continue

            home_elo = current_ratings.get(home_id, DEFAULT_ELO)
            away_elo = current_ratings.get(away_id, DEFAULT_ELO)

            result = engine.rate_game(
                home_elo=home_elo,
                away_elo=away_elo,
                home_score=game.home_score,
                away_score=game.away_score,
            )

            new_home_elo = result["home_new"]
            new_away_elo = result["away_new"]

            current_ratings[home_id] = new_home_elo
            current_ratings[away_id] = new_away_elo

            elo_records_to_create.append(
                EloRating(
                    team=game.home_team,
                    season=game.season,
                    date=game.game_date,
                    rating=round(new_home_elo, 4),
                    game=game,
                )
            )
            elo_records_to_create.append(
                EloRating(
                    team=game.away_team,
                    season=game.season,
                    date=game.game_date,
                    rating=round(new_away_elo, 4),
                    game=game,
                )
            )
            processed += 1

        # Bulk-create; ignore conflicts for idempotency when not resetting
        if elo_records_to_create:
            EloRating.objects.bulk_create(
                elo_records_to_create,
                update_conflicts=True,
                update_fields=["rating"],
                unique_fields=["team", "game"],
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"  [{sport}] Processed {processed} games, skipped {skipped}."
            )
        )

        if not current_ratings:
            return

        # Report top 5 and bottom 5 Elo ratings
        sorted_ratings = sorted(
            current_ratings.items(), key=lambda x: x[1], reverse=True
        )
        from sports.models import Team

        team_map = {
            t.pk: t
            for t in Team.objects.filter(pk__in=list(current_ratings.keys()))
        }

        self.stdout.write(f"\n  [{sport}] Top 5 Elo ratings:")
        for team_id, rating in sorted_ratings[:5]:
            team = team_map.get(team_id)
            name = team.name if team else f"Team #{team_id}"
            self.stdout.write(f"    {name:<30} {rating:.1f}")

        self.stdout.write(f"\n  [{sport}] Bottom 5 Elo ratings:")
        for team_id, rating in sorted_ratings[-5:]:
            team = team_map.get(team_id)
            name = team.name if team else f"Team #{team_id}"
            self.stdout.write(f"    {name:<30} {rating:.1f}")

        self.stdout.write("")
