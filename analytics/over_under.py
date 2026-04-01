"""
Over/under (game total) prediction model for sports betting analytics.

Estimates expected game totals from pace/efficiency ratings and Elo ratings.
No Django imports — pure computation only.
"""

from __future__ import annotations

import math
from typing import Optional

from scipy.stats import norm  # type: ignore[import]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Approximate league-average game totals used as a starting baseline
LEAGUE_AVG_TOTALS: dict[str, float] = {
    "NFL":    47.5,
    "NBA":   225.0,
    "NHL":     6.0,
    "MLB":     8.8,
    "SOCCER":  2.65,
}

# Historical standard deviations of final game totals (empirical)
SPORT_STD_DEVS: dict[str, float] = {
    "NFL":    14.0,
    "NBA":    20.0,
    "NHL":     1.3,
    "MLB":     2.2,
    "SOCCER":  1.0,
}

# How strongly Elo differential compresses or expands the expected total.
# Interpretation: per 100-point Elo gap, adjust total by this fraction.
_ELO_TOTAL_FACTOR: dict[str, float] = {
    "NFL":    0.005,   # blowouts tend to have *lower* totals (clock management)
    "NBA":    0.002,   # small effect
    "NHL":    0.010,   # lopsided games often have fewer goals
    "MLB":    0.008,
    "SOCCER": 0.015,
}


# ---------------------------------------------------------------------------
# TotalModel class
# ---------------------------------------------------------------------------


class TotalModel:
    """Over/under prediction model for a single sport."""

    def __init__(self, sport: str) -> None:
        self.sport = sport.upper()
        self.league_avg: float = LEAGUE_AVG_TOTALS.get(self.sport, 50.0)
        self.std_dev: float = SPORT_STD_DEVS.get(self.sport, 10.0)

    # ------------------------------------------------------------------
    # Total prediction
    # ------------------------------------------------------------------

    def predict_total(
        self,
        home_pace: float,
        away_pace: float,
        home_def_rating: float,
        away_def_rating: float,
        league_avg: Optional[float] = None,
    ) -> float:
        """Predict the expected game total from pace and defensive ratings.

        A simple four-factor model:
            expected_total = ((home_pace + away_pace) / 2)
                             * ((home_def_rating + away_def_rating) / 2)
                             / league_avg_def_rating

        For sports where pace already encodes scoring rate (NBA possessions),
        callers should pass pace values already normalised to points/game.

        Parameters
        ----------
        home_pace, away_pace:
            Offensive pace/scoring rate of each team (same units as total —
            e.g. points/game for NBA, goals/game for NHL).
        home_def_rating, away_def_rating:
            Points/goals allowed per game by each team's defence.
        league_avg:
            Override league-average total; defaults to the sport preset.

        Returns
        -------
        Predicted total (float).
        """
        base = league_avg if league_avg is not None else self.league_avg

        # Average offensive output vs average defence faced
        avg_off = (home_pace + away_pace) / 2.0
        avg_def = (home_def_rating + away_def_rating) / 2.0

        # Scale relative to league average on each dimension
        off_ratio = avg_off / base if base > 0 else 1.0
        def_ratio = avg_def / base if base > 0 else 1.0

        # Geometric blend: off drives scoring up, def drives it down
        predicted = base * math.sqrt(off_ratio * def_ratio)
        return max(0.0, predicted)

    # ------------------------------------------------------------------
    # Over/under probability
    # ------------------------------------------------------------------

    def over_probability(
        self,
        predicted_total: float,
        line: float,
        std_dev: Optional[float] = None,
    ) -> float:
        """Probability that the actual total exceeds *line*.

        Assumes a normal distribution centred on *predicted_total* with the
        sport's historical standard deviation.

        Parameters
        ----------
        predicted_total:
            Model's point estimate for the game total.
        line:
            The bookmaker's over/under line.
        std_dev:
            Override the standard deviation; defaults to the sport preset.

        Returns
        -------
        Probability in [0.01, 0.99].
        """
        sd = std_dev if std_dev is not None else self.std_dev
        if sd <= 0:
            return 1.0 if predicted_total > line else 0.0

        # P(X > line) where X ~ N(predicted_total, sd)
        prob = float(norm.sf(line, loc=predicted_total, scale=sd))
        return max(0.01, min(prob, 0.99))

    def under_probability(
        self,
        predicted_total: float,
        line: float,
        std_dev: Optional[float] = None,
    ) -> float:
        """Probability that the actual total falls below *line*."""
        return 1.0 - self.over_probability(predicted_total, line, std_dev)


# ---------------------------------------------------------------------------
# Module-level utility functions
# ---------------------------------------------------------------------------


def predict_total(
    home_pace: float,
    away_pace: float,
    home_def_rating: float,
    away_def_rating: float,
    league_avg: float,
) -> float:
    """Stateless wrapper around ``TotalModel.predict_total``.

    Useful when the caller does not need the full model object.
    """
    base = league_avg
    avg_off = (home_pace + away_pace) / 2.0
    avg_def = (home_def_rating + away_def_rating) / 2.0
    off_ratio = avg_off / base if base > 0 else 1.0
    def_ratio = avg_def / base if base > 0 else 1.0
    predicted = base * math.sqrt(off_ratio * def_ratio)
    return max(0.0, predicted)


def over_probability(
    predicted_total: float,
    line: float,
    std_dev: Optional[float] = None,
    sport: Optional[str] = None,
) -> float:
    """Stateless over-probability calculator.

    Parameters
    ----------
    predicted_total:
        Model's point estimate for the game total.
    line:
        The bookmaker's over/under line.
    std_dev:
        Standard deviation override.  If None, falls back to *sport* lookup,
        then to a conservative default of 10.
    sport:
        Used to look up the default standard deviation if *std_dev* is None.
    """
    if std_dev is None:
        if sport is not None:
            std_dev = SPORT_STD_DEVS.get(sport.upper(), 10.0)
        else:
            std_dev = 10.0

    if std_dev <= 0:
        return 1.0 if predicted_total > line else 0.0

    prob = float(norm.sf(line, loc=predicted_total, scale=std_dev))
    return max(0.01, min(prob, 0.99))


def expected_total_from_elo(
    home_elo: float,
    away_elo: float,
    league_avg_total: float,
    sport: str,
) -> float:
    """Rough game-total estimate derived from Elo ratings alone.

    A larger Elo differential between the teams slightly lowers the expected
    total — the stronger side tends to control pace and/or the weaker team
    cannot keep up offensively.

    Parameters
    ----------
    home_elo, away_elo:
        Current Elo ratings (no home-advantage applied here).
    league_avg_total:
        The league-average game total to anchor the estimate.
    sport:
        Used to look up the Elo-to-total sensitivity factor.

    Returns
    -------
    Predicted total (float).
    """
    sport = sport.upper()
    factor = _ELO_TOTAL_FACTOR.get(sport, 0.005)
    elo_diff = abs(home_elo - away_elo)

    # Each 100-point gap reduces the total by `factor * 100` units
    reduction = elo_diff * factor
    predicted = league_avg_total - reduction
    return max(0.0, predicted)
