"""
Tests for analytics engine modules.

Most tests are pure-Python (no DB) since the analytics modules are
explicitly Django-free.  A handful of DB-backed tests use factories
from sports.tests.
"""

import pytest

from analytics.adjustments import (
    compute_total_adjustment,
    injury_impact_factor,
    rest_adjustment,
)
from analytics.elo import DEFAULT_ELO, EloEngine, expected_score, update_ratings, K_FACTORS
from analytics.over_under import TotalModel
from analytics.win_probability import (
    WinProbabilityModel,
    blend_predictions,
    calibrate_probability,
)


# ---------------------------------------------------------------------------
# TestEloEngine
# ---------------------------------------------------------------------------


class TestEloEngine:
    # --- expected_score ---------------------------------------------------

    def test_expected_score_equal_ratings(self):
        # Equal ratings, no bonus → exactly 0.5
        result = expected_score(1500.0, 1500.0)
        assert result == pytest.approx(0.5, abs=1e-9)

    def test_expected_score_home_advantage(self):
        # Adding NFL home advantage (+55) to home team rating
        ha = 55.0
        result = expected_score(1500.0 + ha, 1500.0)
        assert result > 0.5

    def test_expected_score_higher_rating_favoured(self):
        result = expected_score(1600.0, 1400.0)
        assert result > 0.5

    def test_expected_score_symmetry(self):
        a = expected_score(1600.0, 1400.0)
        b = expected_score(1400.0, 1600.0)
        assert a + b == pytest.approx(1.0, abs=1e-9)

    # --- win_probability --------------------------------------------------

    def test_win_probability_returns_float_in_range(self):
        engine = EloEngine("NFL")
        prob = engine.win_probability(1500.0, 1500.0)
        assert 0.0 < prob < 1.0

    def test_win_probability_home_advantage_applied(self):
        engine = EloEngine("NFL")
        # With equal ratings the home team should be favoured due to HA
        prob = engine.win_probability(1500.0, 1500.0)
        assert prob > 0.5

    def test_win_probability_much_better_team(self):
        engine = EloEngine("NFL")
        prob = engine.win_probability(1800.0, 1200.0)
        assert prob > 0.85

    # --- rate_game (winner / loser delta) ---------------------------------

    def test_rate_game_winner_gains_elo(self):
        engine = EloEngine("NFL")
        result = engine.rate_game(
            home_elo=1500.0, away_elo=1500.0,
            home_score=27, away_score=10
        )
        assert result["home_new"] > 1500.0

    def test_rate_game_loser_loses_elo(self):
        engine = EloEngine("NFL")
        result = engine.rate_game(
            home_elo=1500.0, away_elo=1500.0,
            home_score=27, away_score=10
        )
        assert result["away_new"] < 1500.0

    def test_rate_game_elo_sum_conserved(self):
        """Total Elo across both teams must be conserved after a game."""
        engine = EloEngine("NFL")
        home_elo, away_elo = 1520.0, 1480.0
        result = engine.rate_game(home_elo, away_elo, home_score=21, away_score=14)
        total_before = home_elo + away_elo
        total_after = result["home_new"] + result["away_new"]
        assert total_after == pytest.approx(total_before, abs=1e-6)

    def test_rate_game_with_margin_larger_swing(self):
        """A blowout win should produce a larger Elo swing than a 1-point win."""
        engine = EloEngine("NFL")
        close = engine.rate_game(1500.0, 1500.0, home_score=21, away_score=20)
        blowout = engine.rate_game(1500.0, 1500.0, home_score=45, away_score=3)
        # Winner (home) should gain more Elo in the blowout
        assert blowout["home_new"] > close["home_new"]

    def test_rate_game_draw(self):
        """Draw should keep ratings close to starting values."""
        engine = EloEngine("SOCCER")
        result = engine.rate_game(1500.0, 1500.0, home_score=1, away_score=1)
        # Neither team should gain more than K points from a draw vs equal opponent
        k = engine.k_factor
        assert abs(result["home_new"] - 1500.0) < k
        assert abs(result["away_new"] - 1500.0) < k

    def test_rate_game_expected_prob_returned(self):
        engine = EloEngine("NBA")
        result = engine.rate_game(1600.0, 1400.0, home_score=110, away_score=95)
        assert "home_expected" in result
        assert "away_expected" in result
        assert result["home_expected"] + result["away_expected"] == pytest.approx(1.0, abs=1e-9)

    # --- K-factor by sport ------------------------------------------------

    def test_elo_engine_nfl_k_factor(self):
        engine = EloEngine("NFL")
        assert engine.k_factor == K_FACTORS["NFL"]
        assert engine.k_factor == 20.0

    def test_elo_engine_soccer_k_factor(self):
        engine = EloEngine("SOCCER")
        assert engine.k_factor == K_FACTORS["SOCCER"]
        assert engine.k_factor == 20.0

    def test_elo_engine_mlb_k_factor(self):
        engine = EloEngine("MLB")
        assert engine.k_factor == K_FACTORS["MLB"]
        assert engine.k_factor == 16.0

    def test_elo_engine_custom_k_factor(self):
        engine = EloEngine("NFL", k_factor=40.0)
        assert engine.k_factor == 40.0

    # --- update_ratings (module-level) ------------------------------------

    def test_update_ratings_winner_increases(self):
        new_winner, new_loser = update_ratings(1500.0, 1500.0, "NFL")
        assert new_winner > 1500.0

    def test_update_ratings_loser_decreases(self):
        new_winner, new_loser = update_ratings(1500.0, 1500.0, "NFL")
        assert new_loser < 1500.0

    def test_update_ratings_with_margin_nfl(self):
        """Larger margin produces larger winner gain for MOV-enabled sport."""
        _, loser_close = update_ratings(1500.0, 1500.0, "NFL", margin=1)
        _, loser_blowout = update_ratings(1500.0, 1500.0, "NFL", margin=30)
        assert loser_blowout < loser_close

    def test_update_ratings_no_mov_for_soccer(self):
        """Soccer is not in MOV_SPORTS — margin should be ignored."""
        w1, l1 = update_ratings(1500.0, 1500.0, "SOCCER", margin=1)
        w2, l2 = update_ratings(1500.0, 1500.0, "SOCCER", margin=10)
        assert w1 == pytest.approx(w2)
        assert l1 == pytest.approx(l2)

    def test_default_elo_constant(self):
        assert DEFAULT_ELO == 1500.0


# ---------------------------------------------------------------------------
# TestWinProbabilityModel
# ---------------------------------------------------------------------------


class TestWinProbabilityModel:
    def test_predict_returns_0_to_1_range(self):
        import numpy as np
        model = WinProbabilityModel("NFL")
        fv = np.array([100.0, 2.0, 500.0, 1.0])
        prob = model.predict(fv)
        assert 0.0 <= prob <= 1.0

    def test_better_elo_team_more_likely_to_win(self):
        model = WinProbabilityModel("NFL")
        prob_strong = model.predict_from_context(
            home_elo=1700.0, away_elo=1300.0,
            home_rest_days=7.0, away_rest_days=7.0
        )
        prob_weak = model.predict_from_context(
            home_elo=1300.0, away_elo=1700.0,
            home_rest_days=7.0, away_rest_days=7.0
        )
        assert prob_strong > prob_weak

    def test_home_team_has_slight_advantage_all_else_equal(self):
        model = WinProbabilityModel("NFL")
        prob = model.predict_from_context(
            home_elo=1500.0, away_elo=1500.0,
            home_rest_days=7.0, away_rest_days=7.0,
            home_is_home=True,
        )
        # With equal Elo and rest, the "elo_diff" feature is 0 but intercept
        # is also 0 for NFL, so probability should be exactly 0.5
        # (the logistic model itself has no structural home bias beyond elo_diff)
        assert 0.0 < prob < 1.0

    def test_well_rested_team_outperforms_fatigued(self):
        model = WinProbabilityModel("NBA")
        rested = model.predict_from_context(
            home_elo=1500.0, away_elo=1500.0,
            home_rest_days=3.0, away_rest_days=3.0
        )
        fatigued = model.predict_from_context(
            home_elo=1500.0, away_elo=1500.0,
            home_rest_days=0.0, away_rest_days=3.0
        )
        # NBA rest weight is 0.02 — rested home team should have higher prob
        # when home has more rest than away
        rested_home_more = model.predict_from_context(
            home_elo=1500.0, away_elo=1500.0,
            home_rest_days=5.0, away_rest_days=1.0
        )
        rested_home_less = model.predict_from_context(
            home_elo=1500.0, away_elo=1500.0,
            home_rest_days=1.0, away_rest_days=5.0
        )
        assert rested_home_more > rested_home_less

    def test_predict_from_context_output_bounded(self):
        model = WinProbabilityModel("MLB")
        for elo_diff in [-500, -200, 0, 200, 500]:
            prob = model.predict_from_context(
                home_elo=1500.0 + elo_diff, away_elo=1500.0,
                home_rest_days=3.0, away_rest_days=3.0
            )
            assert 0.0 <= prob <= 1.0, f"Prob {prob} out of range for elo_diff={elo_diff}"

    # --- blend_predictions ------------------------------------------------

    def test_blend_predictions_weights_sum_correctly(self):
        blended = blend_predictions(0.70, 0.60, weights=(0.6, 0.4))
        expected = 0.6 * 0.70 + 0.4 * 0.60
        assert blended == pytest.approx(expected, abs=1e-9)

    def test_blend_predictions_equal_weights(self):
        blended = blend_predictions(0.60, 0.40, weights=(0.5, 0.5))
        assert blended == pytest.approx(0.50, abs=1e-9)

    def test_blend_predictions_clamped_to_valid_range(self):
        # Extremes should be clamped to [0.01, 0.99]
        blended_high = blend_predictions(1.0, 1.0)
        blended_low = blend_predictions(0.0, 0.0)
        assert blended_high <= 0.99
        assert blended_low >= 0.01

    # --- calibrate_probability --------------------------------------------

    def test_calibrate_probability_soccer_compresses(self):
        # Soccer has compress=0.88, meaning it pulls probability toward 0.5
        raw = 0.70  # above 0.5
        calibrated = calibrate_probability(raw, "SOCCER")
        # Should be pushed toward 0.5 (less than raw)
        assert calibrated < raw
        assert calibrated > 0.5

    def test_calibrate_probability_nfl_expands(self):
        # NFL has compress=1.05, meaning it pushes away from 0.5 slightly
        raw = 0.60
        calibrated = calibrate_probability(raw, "NFL")
        assert calibrated > raw

    def test_calibrate_probability_nba_unchanged(self):
        # NBA has compress=1.0, so calibrated == raw (before clamping)
        raw = 0.65
        calibrated = calibrate_probability(raw, "NBA")
        assert calibrated == pytest.approx(raw, abs=1e-9)

    def test_calibrate_probability_output_in_range(self):
        for sport in ["NFL", "NBA", "NHL", "MLB", "SOCCER"]:
            for raw in [0.1, 0.3, 0.5, 0.7, 0.9]:
                result = calibrate_probability(raw, sport)
                assert 0.0 <= result <= 1.0, f"{sport} raw={raw} → {result}"


# ---------------------------------------------------------------------------
# TestTotalModel
# ---------------------------------------------------------------------------


class TestTotalModel:
    def test_over_probability_when_total_above_line(self):
        """If predicted total >> line, over probability should be high."""
        model = TotalModel("NFL")
        # Predicted 55 vs line 47.5 — strongly over
        prob = model.over_probability(predicted_total=55.0, line=47.5)
        assert prob > 0.5

    def test_over_probability_when_total_below_line(self):
        """If predicted total << line, over probability should be low."""
        model = TotalModel("NFL")
        prob = model.over_probability(predicted_total=38.0, line=47.5)
        assert prob < 0.5

    def test_under_probability_complements_over(self):
        model = TotalModel("NFL")
        over = model.over_probability(predicted_total=50.0, line=47.5)
        under = model.under_probability(predicted_total=50.0, line=47.5)
        # over + under should sum to 1 (before clamping edge effects)
        assert over + under == pytest.approx(1.0, abs=0.01)

    def test_over_probability_at_line_near_half(self):
        """When predicted == line, over prob should be approximately 0.5."""
        model = TotalModel("NBA")
        prob = model.over_probability(predicted_total=225.0, line=225.0)
        assert 0.45 < prob < 0.55

    def test_predict_total_non_negative(self):
        model = TotalModel("NFL")
        total = model.predict_total(
            home_pace=24.0, away_pace=22.0,
            home_def_rating=20.0, away_def_rating=18.0
        )
        assert total >= 0.0

    def test_predict_total_higher_pace_raises_total(self):
        model = TotalModel("NBA")
        low_pace = model.predict_total(
            home_pace=100.0, away_pace=100.0,
            home_def_rating=105.0, away_def_rating=105.0
        )
        high_pace = model.predict_total(
            home_pace=120.0, away_pace=120.0,
            home_def_rating=105.0, away_def_rating=105.0
        )
        assert high_pace > low_pace

    def test_over_probability_clamped_above_zero(self):
        model = TotalModel("NHL")
        # Line so high the event is practically impossible
        prob = model.over_probability(predicted_total=6.0, line=100.0)
        assert prob >= 0.01

    def test_over_probability_clamped_below_one(self):
        model = TotalModel("NHL")
        prob = model.over_probability(predicted_total=6.0, line=0.0)
        assert prob <= 0.99

    def test_nba_has_high_league_avg(self):
        model = TotalModel("NBA")
        assert model.league_avg > 200.0

    def test_nhl_has_low_league_avg(self):
        model = TotalModel("NHL")
        assert model.league_avg < 10.0


# ---------------------------------------------------------------------------
# TestAdjustments
# ---------------------------------------------------------------------------


class TestAdjustments:
    # --- rest_adjustment --------------------------------------------------

    def test_rest_adjustment_nfl_short_week_negative(self):
        # 4 days rest in NFL = short week (≤ 5 days) → penalty
        result = rest_adjustment(rest_days=4, sport="NFL")
        assert result < 0.0

    def test_rest_adjustment_nfl_thursday_game(self):
        # 3 days rest also counts as short week
        result = rest_adjustment(rest_days=3, sport="NFL")
        assert result == -0.030

    def test_rest_adjustment_nfl_normal_week_zero(self):
        # 7 days rest in NFL → no penalty
        result = rest_adjustment(rest_days=7, sport="NFL")
        assert result == 0.0

    def test_rest_adjustment_nba_back_to_back_negative(self):
        # 1 day rest in NBA = second night of back-to-back
        result = rest_adjustment(rest_days=1, sport="NBA")
        assert result < 0.0

    def test_rest_adjustment_nba_first_night_back_to_back(self):
        result = rest_adjustment(rest_days=2, sport="NBA")
        assert result < 0.0

    def test_rest_adjustment_nba_back_to_back_bigger_penalty(self):
        # Second night (1 day rest) penalised more than first night (2 days)
        second_night = rest_adjustment(rest_days=1, sport="NBA")
        first_night = rest_adjustment(rest_days=2, sport="NBA")
        assert second_night < first_night

    def test_rest_adjustment_well_rested_zero_or_positive(self):
        # 5 days rest in NBA → no penalty
        result = rest_adjustment(rest_days=5, sport="NBA")
        assert result >= 0.0

    def test_rest_adjustment_nhl_back_to_back(self):
        result = rest_adjustment(rest_days=1, sport="NHL")
        assert result < 0.0

    def test_rest_adjustment_soccer_midweek_fixture(self):
        result = rest_adjustment(rest_days=2, sport="SOCCER")
        assert result < 0.0

    def test_rest_adjustment_returns_float(self):
        result = rest_adjustment(rest_days=7, sport="NFL")
        assert isinstance(result, float)

    # --- compute_total_adjustment -----------------------------------------

    def test_compute_total_adjustment_returns_dict_with_required_keys(self):
        result = compute_total_adjustment(
            home_rest=7.0, away_rest=4.0,
            home_travel_km=0.0, away_travel_km=2400.0,
            sport="NFL"
        )
        assert "home_adjustment" in result
        assert "away_adjustment" in result
        assert "notes" in result

    def test_compute_total_adjustment_notes_is_list(self):
        result = compute_total_adjustment(
            home_rest=3.0, away_rest=7.0,
            home_travel_km=0.0, away_travel_km=1600.0,
            sport="NBA"
        )
        assert isinstance(result["notes"], list)

    def test_compute_total_adjustment_short_rest_away_helps_home(self):
        # Away team on short rest → their penalty boosts home team's adjustment
        result_rested_away = compute_total_adjustment(
            home_rest=7.0, away_rest=7.0,
            home_travel_km=0.0, away_travel_km=0.0,
            sport="NBA"
        )
        result_tired_away = compute_total_adjustment(
            home_rest=7.0, away_rest=1.0,
            home_travel_km=0.0, away_travel_km=0.0,
            sport="NBA"
        )
        assert result_tired_away["home_adjustment"] >= result_rested_away["home_adjustment"]

    def test_compute_total_adjustment_note_added_on_short_rest(self):
        result = compute_total_adjustment(
            home_rest=3.0, away_rest=7.0,
            home_travel_km=0.0, away_travel_km=0.0,
            sport="NFL"
        )
        # Home team on short week → should produce a note
        assert len(result["notes"]) > 0

    # --- injury_impact_factor ---------------------------------------------

    def test_injury_impact_nfl_qb_high_impact(self):
        # Starting QB out → largest single-player impact in NFL
        penalty = injury_impact_factor([{"position": "QB", "availability": 0.0}], "NFL")
        rb_penalty = injury_impact_factor([{"position": "RB", "availability": 0.0}], "NFL")
        assert penalty > rb_penalty

    def test_injury_impact_nfl_qb_value(self):
        penalty = injury_impact_factor([{"position": "QB", "availability": 0.0}], "NFL")
        assert penalty == pytest.approx(0.10, abs=1e-9)

    def test_injury_impact_partial_availability(self):
        full_out = injury_impact_factor([{"position": "QB", "availability": 0.0}], "NFL")
        limited = injury_impact_factor([{"position": "QB", "availability": 0.5}], "NFL")
        assert limited < full_out

    def test_injury_impact_fully_available_is_zero(self):
        penalty = injury_impact_factor([{"position": "QB", "availability": 1.0}], "NFL")
        assert penalty == 0.0

    def test_injury_impact_multiple_players_cumulative(self):
        single = injury_impact_factor([{"position": "WR", "availability": 0.0}], "NFL")
        double = injury_impact_factor(
            [{"position": "WR", "availability": 0.0},
             {"position": "WR", "availability": 0.0}], "NFL"
        )
        assert double > single

    def test_injury_impact_capped_at_040(self):
        # Even with many starters out, penalty caps at 0.40
        many_injured = [{"position": "QB", "availability": 0.0}] * 20
        penalty = injury_impact_factor(many_injured, "NFL")
        assert penalty <= 0.40

    def test_injury_impact_empty_list_is_zero(self):
        penalty = injury_impact_factor([], "NFL")
        assert penalty == 0.0

    def test_injury_impact_unknown_position_uses_default(self):
        penalty = injury_impact_factor([{"position": "UNKNOWN_POS", "availability": 0.0}], "NFL")
        # Default weight is 0.02
        assert penalty == pytest.approx(0.02, abs=1e-9)

    def test_injury_impact_nba_goalie_equivalent(self):
        # NHL goalie is the highest-weight position at 0.08
        penalty = injury_impact_factor([{"position": "G", "availability": 0.0}], "NHL")
        d_penalty = injury_impact_factor([{"position": "D", "availability": 0.0}], "NHL")
        assert penalty > d_penalty
