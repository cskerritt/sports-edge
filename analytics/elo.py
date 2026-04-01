"""
Elo rating system for sports betting analytics.

Sport-specific K-factors, home advantages, and margin-of-victory multipliers.
No Django imports — pure computation only.
"""

from __future__ import annotations

import math
from typing import Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_ELO: float = 1500.0

# Base K-factors per sport
K_FACTORS: dict[str, float] = {
    "NFL": 20.0,
    "NBA": 20.0,
    "NHL": 20.0,
    "MLB": 16.0,
    "SOCCER": 20.0,
}

# Home-team Elo bonus (added before expected-score calculation)
HOME_ADVANTAGES: dict[str, float] = {
    "NFL": 55.0,
    "NBA": 100.0,
    "NHL": 50.0,
    "MLB": 40.0,
    "SOCCER": 60.0,
}

# Sports that support margin-of-victory multiplier
MOV_SPORTS: frozenset[str] = frozenset({"NFL", "NBA", "NHL"})


# ---------------------------------------------------------------------------
# Core Elo functions
# ---------------------------------------------------------------------------


def expected_score(rating_a: float, rating_b: float) -> float:
    """Return the expected score for team A given ratings A and B.

    Uses the standard Elo formula:
        E_a = 1 / (1 + 10^((rating_b - rating_a) / 400))
    """
    return 1.0 / (1.0 + 10.0 ** ((rating_b - rating_a) / 400.0))


def _mov_multiplier(margin: float, winner_elo_diff: float) -> float:
    """Compute the margin-of-victory multiplier.

    Formula:
        ln(|margin| + 1) * (2.2 / (winner_elo_diff * 0.001 + 2.2))

    Clamped to [1.0, 3.0] to prevent extreme adjustments.
    """
    if margin == 0:
        return 1.0
    raw = math.log(abs(margin) + 1) * (2.2 / (abs(winner_elo_diff) * 0.001 + 2.2))
    return max(1.0, min(raw, 3.0))


def update_ratings(
    winner_rating: float,
    loser_rating: float,
    sport: str,
    margin: Optional[float] = None,
    home_advantage: bool = True,
) -> tuple[float, float]:
    """Update Elo ratings after a game result.

    Parameters
    ----------
    winner_rating:
        Pre-game Elo of the winning team.
    loser_rating:
        Pre-game Elo of the losing team.
    sport:
        One of NFL, NBA, NHL, MLB, SOCCER (case-insensitive).
    margin:
        Absolute point/goal margin of victory.  Required for MOV adjustment.
    home_advantage:
        Whether the winner is the home team.  Adds the sport-specific home
        bonus to the winner's rating before computing expected score.

    Returns
    -------
    (new_winner_elo, new_loser_elo)
    """
    sport = sport.upper()
    k = K_FACTORS.get(sport, 20.0)
    ha = HOME_ADVANTAGES.get(sport, 65.0) if home_advantage else 0.0

    # Apply home advantage to winner's effective rating for E calculation
    effective_winner = winner_rating + ha
    e_winner = expected_score(effective_winner, loser_rating)
    e_loser = 1.0 - e_winner

    # Margin-of-victory multiplier
    if sport in MOV_SPORTS and margin is not None:
        elo_diff = effective_winner - loser_rating
        mov = _mov_multiplier(margin, elo_diff)
    else:
        mov = 1.0

    # Actual scores: winner = 1, loser = 0
    delta_winner = k * mov * (1.0 - e_winner)
    delta_loser = k * mov * (0.0 - e_loser)

    return winner_rating + delta_winner, loser_rating + delta_loser


# ---------------------------------------------------------------------------
# EloEngine class
# ---------------------------------------------------------------------------


class EloEngine:
    """High-level Elo engine for a single sport."""

    def __init__(self, sport: str, k_factor: Optional[float] = None) -> None:
        self.sport = sport.upper()
        self.k_factor: float = k_factor if k_factor is not None else K_FACTORS.get(self.sport, 20.0)
        self.home_advantage: float = HOME_ADVANTAGES.get(self.sport, 65.0)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def win_probability(self, home_elo: float, away_elo: float) -> float:
        """Return the home team's win probability (0–1).

        Home advantage is applied by adding the sport-specific bonus to the
        home team's rating before computing the expected score.
        """
        return expected_score(home_elo + self.home_advantage, away_elo)

    def expected_total(
        self,
        home_elo: float,
        away_elo: float,
        league_avg_total: float,
    ) -> float:
        """Rough over/under estimate derived from Elo ratings.

        A higher Elo gap tends to compress totals (favoured team defends
        its lead).  We adjust the league average by a small factor of the
        rating difference.
        """
        elo_diff = abs(home_elo - away_elo)
        # Each 100-point Elo gap shrinks the expected total by ~1 %
        adjustment_factor = 1.0 - (elo_diff / 10_000.0)
        adjustment_factor = max(0.90, min(adjustment_factor, 1.05))
        return league_avg_total * adjustment_factor

    def rate_game(
        self,
        home_elo: float,
        away_elo: float,
        home_score: float,
        away_score: float,
    ) -> dict:
        """Process a completed game and return updated ratings + metadata.

        Parameters
        ----------
        home_elo, away_elo:
            Pre-game Elo ratings.
        home_score, away_score:
            Final scores.

        Returns
        -------
        dict with keys:
            home_new       – updated home Elo
            away_new       – updated away Elo
            home_expected  – home win probability (pre-game)
            away_expected  – away win probability (pre-game)
            home_mov_k     – effective K used for home team
            away_mov_k     – effective K used for away team
        """
        home_expected = self.win_probability(home_elo, away_elo)
        away_expected = 1.0 - home_expected

        margin = abs(home_score - away_score)

        if home_score > away_score:
            # Home team won
            winner_elo, loser_elo = home_elo, away_elo
            elo_diff = (home_elo + self.home_advantage) - away_elo
            mov = self._compute_mov(margin, elo_diff)
            home_delta = self.k_factor * mov * (1.0 - home_expected)
            away_delta = self.k_factor * mov * (0.0 - away_expected)
        elif away_score > home_score:
            # Away team won — no home advantage boost for loser
            winner_elo, loser_elo = away_elo, home_elo
            # From the away team's perspective the home advantage hurts them
            elo_diff = away_elo - (home_elo + self.home_advantage)
            mov = self._compute_mov(margin, elo_diff)
            # Away win: actual=1 for away, actual=0 for home
            away_delta = self.k_factor * mov * (1.0 - away_expected)
            home_delta = self.k_factor * mov * (0.0 - home_expected)
        else:
            # Draw — treat as 0.5 actual score for both
            mov = 1.0
            home_delta = self.k_factor * mov * (0.5 - home_expected)
            away_delta = self.k_factor * mov * (0.5 - away_expected)

        home_mov_k = self.k_factor * mov
        away_mov_k = self.k_factor * mov

        return {
            "home_new": home_elo + home_delta,
            "away_new": away_elo + away_delta,
            "home_expected": home_expected,
            "away_expected": away_expected,
            "home_mov_k": home_mov_k,
            "away_mov_k": away_mov_k,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _compute_mov(self, margin: float, elo_diff: float) -> float:
        """Return MOV multiplier if this sport supports it, else 1.0."""
        if self.sport in MOV_SPORTS:
            return _mov_multiplier(margin, elo_diff)
        return 1.0
