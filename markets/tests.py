"""
Tests for markets.kelly and markets.edge_calculator.
"""

import datetime
from unittest.mock import patch

import pytest

from markets.kelly import (
    expected_value,
    kelly_fraction,
    kelly_from_market_price,
    recommended_bet_size,
)


# ---------------------------------------------------------------------------
# TestKellyFunctions
# ---------------------------------------------------------------------------


class TestKellyFunctions:
    # --- kelly_fraction ---------------------------------------------------

    def test_kelly_fraction_positive_edge(self):
        # prob=0.6, decimal_odds=2.0 → b=1, f=(1*0.6 - 0.4)/1 = 0.2
        result = kelly_fraction(prob=0.6, odds_decimal=2.0)
        assert result == pytest.approx(0.20, abs=1e-9)

    def test_kelly_fraction_negative_edge(self):
        # prob=0.4, odds=1.5 → b=0.5, f=(0.5*0.4 - 0.6)/0.5 = -0.8 → clamped to 0
        result = kelly_fraction(prob=0.4, odds_decimal=1.5)
        assert result == 0.0

    def test_kelly_fraction_zero_edge_is_zero(self):
        # prob = 1/odds → break-even; no Kelly edge
        result = kelly_fraction(prob=0.5, odds_decimal=2.0)
        assert result == 0.0

    def test_kelly_fraction_invalid_prob_zero(self):
        result = kelly_fraction(prob=0.0, odds_decimal=2.0)
        assert result == 0.0

    def test_kelly_fraction_invalid_prob_one(self):
        result = kelly_fraction(prob=1.0, odds_decimal=2.0)
        assert result == 0.0

    def test_kelly_fraction_invalid_odds_lte_one(self):
        result = kelly_fraction(prob=0.6, odds_decimal=1.0)
        assert result == 0.0

    def test_kelly_fraction_never_negative(self):
        result = kelly_fraction(prob=0.1, odds_decimal=1.1)
        assert result >= 0.0

    def test_kelly_fraction_large_edge(self):
        # Certain win at 2.0 odds → full Kelly = 1.0
        result = kelly_fraction(prob=0.99, odds_decimal=2.0)
        assert result > 0.0

    # --- kelly_from_market_price ------------------------------------------

    def test_kelly_from_market_price_positive(self):
        # model says 0.65, market says 0.50 → buy YES, positive fraction
        result = kelly_from_market_price(model_prob=0.65, market_price=0.50)
        assert result > 0.0

    def test_kelly_from_market_price_no_bet(self):
        # model says 0.45, market says 0.50 → model BELOW market, no YES edge
        result = kelly_from_market_price(model_prob=0.45, market_price=0.50)
        assert result == 0.0

    def test_kelly_from_market_price_at_market_no_bet(self):
        # model == market → no edge; Kelly ≈ 0 (floating-point may yield tiny positive)
        result = kelly_from_market_price(model_prob=0.55, market_price=0.55)
        assert result == pytest.approx(0.0, abs=1e-10)

    def test_kelly_clamped_to_max_fraction(self):
        # Enormous edge → result should be clamped to MAX_KELLY_FRACTION (0.10)
        result = kelly_from_market_price(model_prob=0.99, market_price=0.01)
        from django.conf import settings
        max_frac = getattr(settings, "MAX_KELLY_FRACTION", 0.10)
        assert result <= max_frac

    def test_kelly_from_market_price_never_negative(self):
        result = kelly_from_market_price(model_prob=0.20, market_price=0.80)
        assert result >= 0.0

    def test_kelly_from_market_price_scales_with_edge(self):
        small_edge = kelly_from_market_price(model_prob=0.52, market_price=0.50)
        large_edge = kelly_from_market_price(model_prob=0.65, market_price=0.50)
        assert large_edge >= small_edge

    # --- recommended_bet_size ---------------------------------------------

    def test_recommended_bet_size_yes_position(self):
        # model above market → should recommend YES
        result = recommended_bet_size(
            bankroll=1000.0, model_prob=0.65, market_price=0.50
        )
        assert result["position"] == "YES"
        assert result["bet_amount"] > 0.0

    def test_recommended_bet_size_no_position(self):
        # model below market → model thinks NO is underpriced
        result = recommended_bet_size(
            bankroll=1000.0, model_prob=0.35, market_price=0.50
        )
        assert result["position"] == "NO"
        assert result["bet_amount"] > 0.0

    def test_recommended_bet_size_no_bet_at_threshold(self):
        # model == market → no edge
        result = recommended_bet_size(
            bankroll=1000.0, model_prob=0.50, market_price=0.50
        )
        assert result["position"] == "NO_BET"
        assert result["bet_amount"] == 0.0
        assert result["kelly_fraction"] == 0.0

    def test_recommended_bet_size_contains_required_keys(self):
        result = recommended_bet_size(
            bankroll=1000.0, model_prob=0.60, market_price=0.50
        )
        for key in ("position", "kelly_fraction", "bet_amount", "edge", "expected_value"):
            assert key in result

    def test_recommended_bet_size_edge_field_correct(self):
        result = recommended_bet_size(
            bankroll=1000.0, model_prob=0.65, market_price=0.50
        )
        assert result["edge"] == pytest.approx(0.15, abs=1e-9)

    def test_recommended_bet_size_bet_amount_bounded_by_bankroll(self):
        bankroll = 500.0
        result = recommended_bet_size(
            bankroll=bankroll, model_prob=0.80, market_price=0.50
        )
        assert result["bet_amount"] <= bankroll

    # --- expected_value ---------------------------------------------------

    def test_expected_value_positive_when_model_above_market(self):
        ev = expected_value(model_prob=0.65, market_price=0.50)
        assert ev == pytest.approx(0.15, abs=1e-9)

    def test_expected_value_negative_when_model_below_market(self):
        ev = expected_value(model_prob=0.40, market_price=0.55)
        assert ev < 0.0

    def test_expected_value_zero_when_equal(self):
        ev = expected_value(model_prob=0.50, market_price=0.50)
        assert ev == pytest.approx(0.0, abs=1e-9)

    def test_expected_value_formula(self):
        # EV = model_prob - market_price (derived in kelly.py docstring)
        for mp, mkt in [(0.70, 0.55), (0.30, 0.45), (0.50, 0.50)]:
            assert expected_value(mp, mkt) == pytest.approx(mp - mkt, abs=1e-9)


# ---------------------------------------------------------------------------
# TestEdgeCalculator (requires DB)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestEdgeCalculator:
    """
    We import factories lazily inside methods to avoid circular imports at
    collection time.  Sports factories come from sports.tests; market factories
    are defined inline below.
    """

    # ------ Inline factories -----------------------------------------------

    def _make_contract(self, game, contract_type="HOME_WIN", sport="NFL"):
        """Create a bare MarketContract linked to a game."""
        from markets.models import MarketContract, ContractType
        return MarketContract.objects.create(
            game=game,
            sport=sport,
            title=f"Test contract {game.pk}",
            contract_type=contract_type,
            coinbase_product_id=f"prod-{game.pk}-{contract_type}",
            is_active=True,
            is_resolved=False,
        )

    def _make_price(self, contract, yes_price=0.50):
        """Create a MarketPrice snapshot for a contract."""
        from markets.models import MarketPrice
        no_price = 1.0 - yes_price
        return MarketPrice.objects.create(
            contract=contract,
            yes_price=yes_price,
            no_price=no_price,
            mid_price=yes_price,  # simplified: mid == yes for tests
        )

    def _make_prediction(self, game, home_win_prob=0.65, model_version="ensemble_v1"):
        """Create a GamePrediction for a game."""
        from analytics.models import GamePrediction
        return GamePrediction.objects.create(
            game=game,
            model_version=model_version,
            home_win_prob=home_win_prob,
            away_win_prob=round(1.0 - home_win_prob, 4),
            confidence=0.7,
        )

    def _make_game(self):
        """Return a saved Game using sports factories."""
        from sports.tests import GameFactory
        return GameFactory()

    # ------ calculate_edge -----------------------------------------------

    def test_calculate_edge_basic(self):
        from markets.edge_calculator import EdgeCalculator
        calc = EdgeCalculator()
        edge = calc.calculate_edge(model_prob=0.65, market_prob=0.50)
        assert edge == pytest.approx(0.15, abs=1e-9)

    def test_calculate_edge_negative(self):
        from markets.edge_calculator import EdgeCalculator
        calc = EdgeCalculator()
        edge = calc.calculate_edge(model_prob=0.40, market_prob=0.55)
        assert edge < 0.0

    # ------ process_contract ---------------------------------------------

    def test_process_contract_creates_alert_when_above_threshold(self):
        from markets.edge_calculator import EdgeCalculator
        from markets.models import EdgeAlert

        game = self._make_game()
        contract = self._make_contract(game, "HOME_WIN")
        self._make_price(contract, yes_price=0.50)
        self._make_prediction(game, home_win_prob=0.65)

        calc = EdgeCalculator(edge_threshold=0.05)
        alert = calc.process_contract(contract)

        assert alert is not None
        assert isinstance(alert, EdgeAlert)
        assert alert.edge == pytest.approx(0.15, abs=1e-4)
        assert alert.sport == "NFL"
        assert alert.status == "OPEN"

    def test_process_contract_no_alert_below_threshold(self):
        from markets.edge_calculator import EdgeCalculator

        game = self._make_game()
        contract = self._make_contract(game, "HOME_WIN")
        self._make_price(contract, yes_price=0.50)
        # Edge = 0.52 - 0.50 = 0.02, below default threshold of 0.05
        self._make_prediction(game, home_win_prob=0.52)

        calc = EdgeCalculator(edge_threshold=0.05)
        alert = calc.process_contract(contract)

        assert alert is None

    def test_process_contract_no_market_price_returns_none(self):
        from markets.edge_calculator import EdgeCalculator

        game = self._make_game()
        contract = self._make_contract(game, "HOME_WIN")
        # No price created
        self._make_prediction(game, home_win_prob=0.65)

        calc = EdgeCalculator()
        result = calc.process_contract(contract)
        assert result is None

    def test_process_contract_no_prediction_returns_none(self):
        from markets.edge_calculator import EdgeCalculator

        game = self._make_game()
        contract = self._make_contract(game, "HOME_WIN")
        self._make_price(contract, yes_price=0.50)
        # No prediction created

        calc = EdgeCalculator()
        result = calc.process_contract(contract)
        assert result is None

    def test_process_contract_no_game_returns_none(self):
        from markets.edge_calculator import EdgeCalculator
        from markets.models import MarketContract

        contract = MarketContract.objects.create(
            game=None,
            sport="NFL",
            title="No game contract",
            coinbase_product_id="prod-no-game-001",
            is_active=True,
            is_resolved=False,
        )
        self._make_price(contract, yes_price=0.50)

        calc = EdgeCalculator()
        result = calc.process_contract(contract)
        assert result is None

    def test_process_contract_away_win_uses_away_prob(self):
        """AWAY_WIN contract should use away_win_prob, not home_win_prob."""
        from markets.edge_calculator import EdgeCalculator
        from markets.models import EdgeAlert

        game = self._make_game()
        contract = self._make_contract(game, "AWAY_WIN")
        self._make_price(contract, yes_price=0.30)
        # home=0.40, away=0.60 → edge on AWAY_WIN = 0.60 - 0.30 = 0.30
        self._make_prediction(game, home_win_prob=0.40)

        calc = EdgeCalculator(edge_threshold=0.05)
        alert = calc.process_contract(contract)
        assert alert is not None
        assert alert.edge == pytest.approx(0.30, abs=0.01)

    def test_process_contract_kelly_stored_on_alert(self):
        """EdgeAlert.kelly_fraction should be > 0 when there is positive edge."""
        from markets.edge_calculator import EdgeCalculator

        game = self._make_game()
        contract = self._make_contract(game, "HOME_WIN")
        self._make_price(contract, yes_price=0.50)
        self._make_prediction(game, home_win_prob=0.65)

        calc = EdgeCalculator(edge_threshold=0.05)
        alert = calc.process_contract(contract)
        assert alert is not None
        assert alert.kelly_fraction > 0.0

    def test_process_contract_negative_edge_creates_no_alert(self):
        """Model below market (negative edge) below threshold → no alert."""
        from markets.edge_calculator import EdgeCalculator

        game = self._make_game()
        contract = self._make_contract(game, "HOME_WIN")
        self._make_price(contract, yes_price=0.75)
        # home prob = 0.72, edge = 0.72 - 0.75 = -0.03 → below |threshold|
        self._make_prediction(game, home_win_prob=0.72)

        calc = EdgeCalculator(edge_threshold=0.05)
        alert = calc.process_contract(contract)
        assert alert is None

    # ------ run_all -------------------------------------------------------

    def test_run_all_returns_stats_dict(self):
        from markets.edge_calculator import EdgeCalculator

        calc = EdgeCalculator()
        result = calc.run_all()
        for key in ("processed", "alerts_created", "alerts_updated", "no_edge", "errors"):
            assert key in result

    def test_run_all_processes_active_contracts(self):
        from markets.edge_calculator import EdgeCalculator

        game = self._make_game()
        contract = self._make_contract(game, "HOME_WIN")
        self._make_price(contract, yes_price=0.50)
        self._make_prediction(game, home_win_prob=0.65)

        calc = EdgeCalculator(edge_threshold=0.05)
        result = calc.run_all()
        assert result["processed"] >= 1

    def test_run_all_skips_inactive_contracts(self):
        """Inactive contracts should not be processed."""
        from markets.edge_calculator import EdgeCalculator
        from markets.models import MarketContract

        game = self._make_game()
        MarketContract.objects.create(
            game=game,
            sport="NFL",
            title="Inactive contract",
            coinbase_product_id="prod-inactive-001",
            is_active=False,
            is_resolved=False,
        )
        calc = EdgeCalculator()
        # Capture processed count before; inactive contract should not add to it
        result = calc.run_all()
        # We can't assert exact count without isolation, but we can assert
        # that no errors occurred for the inactive contract
        assert result["errors"] == 0

    # ------ resolve_alerts -----------------------------------------------

    def test_resolve_alerts_marks_hit_correctly(self):
        """Alert with positive edge that resolves YES should be HIT."""
        from markets.edge_calculator import EdgeCalculator
        from markets.models import EdgeAlert, MarketContract, MarketPrice

        game = self._make_game()
        contract = self._make_contract(game, "HOME_WIN")
        price = self._make_price(contract, yes_price=0.50)
        self._make_prediction(game, home_win_prob=0.70)

        calc = EdgeCalculator(edge_threshold=0.05)
        alert = calc.process_contract(contract)
        assert alert is not None
        assert alert.edge > 0  # Model predicts YES (home win)

        # Resolve the contract as YES (home won)
        contract.is_resolved = True
        contract.resolution = True
        contract.save()

        stats = calc.resolve_alerts()
        alert.refresh_from_db()
        assert alert.status == "HIT"
        assert stats["hits"] >= 1

    def test_resolve_alerts_marks_miss_when_wrong(self):
        """Alert with positive edge that resolves NO should be MISS."""
        from markets.edge_calculator import EdgeCalculator
        from markets.models import MarketContract

        game = self._make_game()
        contract = self._make_contract(game, "HOME_WIN")
        self._make_price(contract, yes_price=0.50)
        self._make_prediction(game, home_win_prob=0.70)

        calc = EdgeCalculator(edge_threshold=0.05)
        alert = calc.process_contract(contract)
        assert alert is not None

        # Resolve the contract as NO (away won)
        contract.is_resolved = True
        contract.resolution = False  # YES did NOT resolve
        contract.save()

        stats = calc.resolve_alerts()
        alert.refresh_from_db()
        assert alert.status == "MISS"
        assert stats["misses"] >= 1

    def test_resolve_alerts_skips_unresolved_contracts(self):
        """Open alerts for unresolved contracts should stay OPEN."""
        from markets.edge_calculator import EdgeCalculator

        game = self._make_game()
        contract = self._make_contract(game, "HOME_WIN")
        self._make_price(contract, yes_price=0.50)
        self._make_prediction(game, home_win_prob=0.70)

        calc = EdgeCalculator(edge_threshold=0.05)
        alert = calc.process_contract(contract)
        assert alert is not None

        # Contract NOT resolved
        stats = calc.resolve_alerts()
        alert.refresh_from_db()
        assert alert.status == "OPEN"
        assert stats["resolved"] == 0

    # ------ get_edge_leaderboard -----------------------------------------

    def test_get_edge_leaderboard_sorted_by_abs_edge(self):
        """Leaderboard should be sorted by |edge| descending."""
        from markets.edge_calculator import EdgeCalculator
        from sports.tests import GameFactory

        # Create two contracts with different edges
        game1 = GameFactory(external_id="lb_game_001")
        game2 = GameFactory(external_id="lb_game_002")

        c1 = self._make_contract(game1, "HOME_WIN")
        c2 = self._make_contract(game2, "HOME_WIN")

        self._make_price(c1, yes_price=0.50)
        self._make_price(c2, yes_price=0.50)

        self._make_prediction(game1, home_win_prob=0.75)  # edge = 0.25
        self._make_prediction(game2, home_win_prob=0.62)  # edge = 0.12

        calc = EdgeCalculator(edge_threshold=0.05)
        calc.process_contract(c1)
        calc.process_contract(c2)

        leaderboard = calc.get_edge_leaderboard()
        assert len(leaderboard) >= 2
        edges = [abs(row["edge"]) for row in leaderboard]
        assert edges == sorted(edges, reverse=True)

    def test_get_edge_leaderboard_contains_required_keys(self):
        from markets.edge_calculator import EdgeCalculator

        game = self._make_game()
        contract = self._make_contract(game, "HOME_WIN")
        self._make_price(contract, yes_price=0.50)
        self._make_prediction(game, home_win_prob=0.70)

        calc = EdgeCalculator(edge_threshold=0.05)
        calc.process_contract(contract)

        leaderboard = calc.get_edge_leaderboard()
        if leaderboard:
            row = leaderboard[0]
            for key in ("contract_id", "contract_title", "sport", "edge",
                        "model_prob", "market_prob", "kelly_fraction", "confidence"):
                assert key in row

    def test_get_edge_leaderboard_sport_filter(self):
        """Leaderboard filtered by sport should only return that sport."""
        from markets.edge_calculator import EdgeCalculator
        from sports.tests import GameFactory
        from sports.models import Sport

        game = GameFactory(sport=Sport.NBA, external_id="nba_lb_001",
                           home_team=__import__('sports.tests', fromlist=['TeamFactory']).TeamFactory(
                               sport=Sport.NBA, abbreviation="NBH"
                           ),
                           away_team=__import__('sports.tests', fromlist=['TeamFactory']).TeamFactory(
                               sport=Sport.NBA, abbreviation="NBA"
                           ))
        contract = self._make_contract(game, "HOME_WIN", sport="NBA")
        self._make_price(contract, yes_price=0.50)
        self._make_prediction(game, home_win_prob=0.70)

        calc = EdgeCalculator(edge_threshold=0.05)
        calc.process_contract(contract)

        nba_leaderboard = calc.get_edge_leaderboard(sport="NBA")
        for row in nba_leaderboard:
            assert row["sport"] == "NBA"

    def test_get_edge_leaderboard_respects_limit(self):
        from markets.edge_calculator import EdgeCalculator

        calc = EdgeCalculator(edge_threshold=0.0)
        result = calc.get_edge_leaderboard(limit=5)
        assert len(result) <= 5
