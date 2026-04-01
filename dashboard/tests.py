"""
Tests for dashboard views.
"""

import datetime

import pytest
from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone

from analytics.models import BacktestResult, GamePrediction
from markets.models import ContractType, EdgeAlert, MarketContract, MarketPrice
from sports.models import Game, GameStatus, Sport
from sports.tests import GameFactory, SeasonFactory, TeamFactory


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_user(username="dash_user", password="securepass123"):
    return User.objects.create_user(
        username=username, password=password, email=f"{username}@example.com"
    )


def login(client, user, password="securepass123"):
    client.login(username=user.username, password=password)


def make_game_today(sport=Sport.NFL, **kwargs):
    today = datetime.date.today()
    defaults = dict(game_date=today, status=GameStatus.SCHEDULED)
    defaults.update(kwargs)
    return GameFactory(sport=sport, **defaults)


def make_game_prediction(game, home_win_prob=0.60):
    return GamePrediction.objects.create(
        game=game,
        model_version="ensemble_v1",
        home_win_prob=home_win_prob,
        away_win_prob=round(1.0 - home_win_prob, 4),
        confidence=0.65,
    )


def make_edge_alert(sport, game=None):
    """Create a MarketContract + price + EdgeAlert for testing leaderboard views."""
    contract = MarketContract.objects.create(
        game=game,
        sport=sport,
        title=f"Test {sport} contract",
        coinbase_product_id=f"prod-dash-{sport}-{id(game)}",
        contract_type=ContractType.HOME_WIN,
        is_active=True,
        is_resolved=False,
    )
    price = MarketPrice.objects.create(
        contract=contract,
        yes_price=0.50,
        no_price=0.50,
        mid_price=0.50,
    )
    alert = EdgeAlert.objects.create(
        contract=contract,
        market_price=price,
        sport=sport,
        model_probability=0.65,
        market_probability=0.50,
        edge=0.15,
        kelly_fraction=0.02,
        confidence=0.70,
        status="OPEN",
    )
    return alert


# ---------------------------------------------------------------------------
# TestDashboardViews
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDashboardViews:
    # --- index ------------------------------------------------------------

    def test_index_requires_login(self, client):
        response = client.get(reverse("dashboard:index"))
        assert response.status_code == 302
        assert "login" in response["Location"].lower()

    def test_index_200_logged_in(self, client):
        user = make_user("dash_index_user")
        login(client, user)
        response = client.get(reverse("dashboard:index"))
        assert response.status_code == 200

    def test_index_context_keys(self, client):
        user = make_user("dash_ctx_user")
        login(client, user)
        response = client.get(reverse("dashboard:index"))
        assert "today_games" in response.context
        assert "top_edges" in response.context
        assert "recent_bets" in response.context
        assert "stats" in response.context

    def test_index_shows_todays_games(self, client):
        user = make_user("dash_today_user")
        login(client, user)

        # Create 3 games today
        for i in range(3):
            make_game_today(external_id=f"idx_game_{i}")

        response = client.get(reverse("dashboard:index"))
        assert response.status_code == 200
        today_games = list(response.context["today_games"])
        assert len(today_games) == 3

    def test_index_excludes_postponed_games(self, client):
        user = make_user("dash_postponed_user")
        login(client, user)

        make_game_today(status=GameStatus.SCHEDULED, external_id="sched_001")
        make_game_today(status=GameStatus.POSTPONED, external_id="post_001")

        response = client.get(reverse("dashboard:index"))
        games = list(response.context["today_games"])
        for g in games:
            assert g.status != GameStatus.POSTPONED

    def test_index_excludes_cancelled_games(self, client):
        user = make_user("dash_cancelled_user")
        login(client, user)

        make_game_today(status=GameStatus.SCHEDULED, external_id="sched_002")
        make_game_today(status=GameStatus.CANCELLED, external_id="cancel_001")

        response = client.get(reverse("dashboard:index"))
        games = list(response.context["today_games"])
        for g in games:
            assert g.status != GameStatus.CANCELLED

    # --- today_games (the stand-alone page) ------------------------------

    def test_today_games_sport_filter(self, client):
        user = make_user("dash_sport_filter_user")
        # Ensure user follows all sports
        user.profile.sports_followed = []
        user.profile.save()
        login(client, user)

        # NFL game today
        nfl_home = TeamFactory(sport=Sport.NFL, abbreviation="NFS")
        nfl_away = TeamFactory(sport=Sport.NFL, abbreviation="NFA")
        GameFactory(
            sport=Sport.NFL,
            home_team=nfl_home, away_team=nfl_away,
            game_date=datetime.date.today(),
            status=GameStatus.SCHEDULED,
            external_id="filter_nfl_001",
        )

        # NBA game today
        nba_home = TeamFactory(sport=Sport.NBA, abbreviation="NBS")
        nba_away = TeamFactory(sport=Sport.NBA, abbreviation="NBA2")
        GameFactory(
            sport=Sport.NBA,
            home_team=nba_home, away_team=nba_away,
            game_date=datetime.date.today(),
            status=GameStatus.SCHEDULED,
            external_id="filter_nba_001",
        )

        url = reverse("dashboard:today") + "?sport=NFL"
        response = client.get(url)
        assert response.status_code == 200
        games = list(response.context["games"])
        for g in games:
            assert g.sport == Sport.NFL

    def test_today_games_no_filter_returns_all_sports(self, client):
        user = make_user("dash_all_sports_user")
        user.profile.sports_followed = []
        user.profile.save()
        login(client, user)

        nfl_home = TeamFactory(sport=Sport.NFL, abbreviation="NS1")
        nfl_away = TeamFactory(sport=Sport.NFL, abbreviation="NA1")
        GameFactory(sport=Sport.NFL, home_team=nfl_home, away_team=nfl_away,
                    game_date=datetime.date.today(), external_id="nf_all_001")

        nba_home = TeamFactory(sport=Sport.NBA, abbreviation="NS2")
        nba_away = TeamFactory(sport=Sport.NBA, abbreviation="NA2")
        GameFactory(sport=Sport.NBA, home_team=nba_home, away_team=nba_away,
                    game_date=datetime.date.today(), external_id="nba_all_001")

        response = client.get(reverse("dashboard:today"))
        games = list(response.context["games"])
        sports_in_response = {g.sport for g in games}
        assert Sport.NFL in sports_in_response
        assert Sport.NBA in sports_in_response

    # --- edge_leaderboard ------------------------------------------------

    def test_edge_leaderboard_200(self, client):
        user = make_user("dash_edges_user")
        login(client, user)
        response = client.get(reverse("dashboard:edges"))
        assert response.status_code == 200

    def test_edge_leaderboard_context_has_edges(self, client):
        user = make_user("dash_edges_ctx_user")
        user.profile.sports_followed = []
        user.profile.save()
        login(client, user)

        make_edge_alert(sport=Sport.NFL)
        make_edge_alert(sport=Sport.NBA)

        response = client.get(reverse("dashboard:edges"))
        assert "edges" in response.context
        assert len(list(response.context["edges"])) >= 2

    def test_edge_leaderboard_sport_filter(self, client):
        user = make_user("dash_edges_filter_user")
        user.profile.sports_followed = []
        user.profile.save()
        login(client, user)

        make_edge_alert(sport=Sport.NFL)
        make_edge_alert(sport=Sport.NBA)

        response = client.get(reverse("dashboard:edges") + "?sport=NFL")
        assert response.status_code == 200
        edges = list(response.context["edges"])
        for e in edges:
            assert e.sport == Sport.NFL

    def test_edge_leaderboard_requires_login(self, client):
        response = client.get(reverse("dashboard:edges"))
        assert response.status_code == 302

    # --- sport_detail ----------------------------------------------------

    def test_sport_detail_valid_sport(self, client):
        user = make_user("dash_sport_detail_user")
        login(client, user)
        response = client.get(reverse("dashboard:sport_detail", kwargs={"sport": "NFL"}))
        assert response.status_code == 200

    def test_sport_detail_valid_sport_nba(self, client):
        user = make_user("dash_sport_nba_user")
        login(client, user)
        response = client.get(reverse("dashboard:sport_detail", kwargs={"sport": "NBA"}))
        assert response.status_code == 200

    def test_sport_detail_invalid_sport_404(self, client):
        user = make_user("dash_invalid_sport_user")
        login(client, user)
        response = client.get(reverse("dashboard:sport_detail", kwargs={"sport": "XYZ"}))
        assert response.status_code == 404

    def test_sport_detail_case_insensitive(self, client):
        user = make_user("dash_sport_case_user")
        login(client, user)
        # URL dispatch passes "nfl" → view calls .upper() → "NFL"
        response = client.get(reverse("dashboard:sport_detail", kwargs={"sport": "nfl"}))
        assert response.status_code == 200

    def test_sport_detail_context_has_required_keys(self, client):
        user = make_user("dash_sport_ctx_user")
        login(client, user)
        response = client.get(reverse("dashboard:sport_detail", kwargs={"sport": "NFL"}))
        for key in ("sport", "upcoming_games", "elo_leaderboard", "backtest_results", "top_edges"):
            assert key in response.context

    def test_sport_detail_requires_login(self, client):
        response = client.get(reverse("dashboard:sport_detail", kwargs={"sport": "NFL"}))
        assert response.status_code == 302

    # --- game_detail -----------------------------------------------------

    def test_game_detail_200(self, client):
        user = make_user("dash_game_detail_user")
        login(client, user)
        game = GameFactory(external_id="detail_test_001")
        response = client.get(reverse("dashboard:game_detail", kwargs={"pk": game.pk}))
        assert response.status_code == 200

    def test_game_detail_404_for_nonexistent(self, client):
        user = make_user("dash_game_404_user")
        login(client, user)
        response = client.get(reverse("dashboard:game_detail", kwargs={"pk": 999999}))
        assert response.status_code == 404

    def test_game_detail_context_has_game(self, client):
        user = make_user("dash_game_ctx_user")
        login(client, user)
        game = GameFactory(external_id="detail_ctx_001")
        response = client.get(reverse("dashboard:game_detail", kwargs={"pk": game.pk}))
        assert response.context["game"].pk == game.pk

    def test_game_detail_context_has_predictions(self, client):
        user = make_user("dash_game_pred_user")
        login(client, user)
        game = GameFactory(external_id="detail_pred_001")
        make_game_prediction(game, home_win_prob=0.62)
        response = client.get(reverse("dashboard:game_detail", kwargs={"pk": game.pk}))
        predictions = list(response.context["predictions"])
        assert len(predictions) == 1
        assert predictions[0].home_win_prob == pytest.approx(0.62)

    def test_game_detail_requires_login(self, client):
        game = GameFactory(external_id="detail_noauth_001")
        response = __import__("django.test", fromlist=["Client"]).Client().get(
            reverse("dashboard:game_detail", kwargs={"pk": game.pk})
        )
        assert response.status_code == 302

    # --- backtest_results ------------------------------------------------

    def test_backtest_results_200(self, client):
        user = make_user("dash_backtest_user")
        login(client, user)
        response = client.get(reverse("dashboard:backtest"))
        assert response.status_code == 200

    def test_backtest_results_empty_state(self, client):
        user = make_user("dash_backtest_empty_user")
        login(client, user)
        response = client.get(reverse("dashboard:backtest"))
        assert response.status_code == 200
        assert response.context["grouped"] == {}

    def test_backtest_results_shows_data(self, client):
        user = make_user("dash_backtest_data_user")
        login(client, user)

        BacktestResult.objects.create(
            sport=Sport.NFL,
            model_version="ensemble_v1",
            total_games=100,
            correct_predictions=58,
            accuracy=0.58,
            brier_score=0.23,
        )
        BacktestResult.objects.create(
            sport=Sport.NBA,
            model_version="ensemble_v1",
            total_games=200,
            correct_predictions=122,
            accuracy=0.61,
        )

        response = client.get(reverse("dashboard:backtest"))
        grouped = response.context["grouped"]
        assert Sport.NFL in grouped
        assert Sport.NBA in grouped

    def test_backtest_results_requires_login(self, client):
        response = client.get(reverse("dashboard:backtest"))
        assert response.status_code == 302

    def test_backtest_context_has_summary_map(self, client):
        user = make_user("dash_backtest_summary_user")
        login(client, user)
        BacktestResult.objects.create(
            sport=Sport.MLB,
            model_version="elo_v1",
            total_games=50,
            correct_predictions=30,
            accuracy=0.60,
        )
        response = client.get(reverse("dashboard:backtest"))
        assert "summary_map" in response.context
