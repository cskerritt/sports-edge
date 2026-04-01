"""
Player prop projection model for sports betting analytics.

Projects player stat lines from rolling game logs and matchup adjustments.
No Django imports — pure computation only.
"""

from __future__ import annotations

import math
from typing import Optional

try:
    import pandas as pd  # type: ignore[import]
    _PANDAS_AVAILABLE = True
except ImportError:
    _PANDAS_AVAILABLE = False

try:
    from scipy.stats import norm as _scipy_norm  # type: ignore[import]
    _SCIPY_AVAILABLE = True
except ImportError:
    _SCIPY_AVAILABLE = False

# ---------------------------------------------------------------------------
# Standard deviations for common prop types (empirical)
# ---------------------------------------------------------------------------

PROP_STD_DEVS: dict[str, float] = {
    "NBA_PTS":       6.5,
    "NBA_REB":       3.0,
    "NBA_AST":       2.5,
    "NFL_PASS_YDS": 55.0,
    "NFL_RUSH_YDS": 28.0,
    "NFL_REC_YDS":  22.0,
    "MLB_STRIKEOUTS": 2.2,
    "NHL_SHOTS":     2.5,
}

# Default std dev when prop type is unknown
_DEFAULT_STD_DEV: float = 5.0

# ---------------------------------------------------------------------------
# Rolling average helpers
# ---------------------------------------------------------------------------

# Weights decay geometrically: most-recent game receives the highest weight.
_DEFAULT_DECAY: float = 0.85  # per-game decay factor (older games weighted less)


def project_rolling_average(
    game_log: list[float],
    window: int = 10,
) -> float:
    """Weighted rolling average with recency bias.

    The most-recent game receives weight 1.0; each prior game is multiplied
    by ``_DEFAULT_DECAY`` relative to the game after it.

    Parameters
    ----------
    game_log:
        Chronological list of per-game stat values (oldest first).
    window:
        Maximum number of recent games to include.

    Returns
    -------
    Weighted average, or 0.0 if ``game_log`` is empty.
    """
    if not game_log:
        return 0.0

    recent = game_log[-window:]  # most recent `window` games (last = newest)
    # Build weights from newest → oldest
    weights = [_DEFAULT_DECAY ** i for i in range(len(recent))]
    weights.reverse()  # align with chronological order (oldest first)

    weighted_sum = sum(v * w for v, w in zip(recent, weights))
    weight_total = sum(weights)

    return weighted_sum / weight_total if weight_total > 0 else 0.0


# ---------------------------------------------------------------------------
# Matchup adjustment
# ---------------------------------------------------------------------------


def project_with_matchup(
    base_projection: float,
    opponent_def_rating: float,
    league_avg_def_rating: float,
    sport: str,
    prop_type: str,
) -> float:
    """Adjust a base projection for the quality of the opponent's defence.

    Parameters
    ----------
    base_projection:
        Rolling-average projection before matchup adjustment.
    opponent_def_rating:
        The opponent defence's stat-allowed rate (same units as the prop).
        Higher = more permissive defence.
    league_avg_def_rating:
        League-average allowed rate for this stat.
    sport:
        Sport identifier (e.g. "NBA", "NFL").
    prop_type:
        Prop category (e.g. "NBA_PTS", "NFL_RUSH_YDS").

    Returns
    -------
    Adjusted projection (float, non-negative).
    """
    if league_avg_def_rating <= 0:
        return base_projection

    # Matchup factor: > 1.0 means favourable matchup (weak defence), < 1.0 tough
    matchup_factor = opponent_def_rating / league_avg_def_rating

    # Dampen the raw factor so one outlier defence doesn't swing too wildly
    # Final factor = 1.0 + 0.5 * (raw_factor - 1.0)  → half-credit
    dampened_factor = 1.0 + 0.5 * (matchup_factor - 1.0)

    adjusted = base_projection * dampened_factor
    return max(0.0, adjusted)


# ---------------------------------------------------------------------------
# Over probability
# ---------------------------------------------------------------------------


def over_probability(
    projected_value: float,
    line: float,
    std_dev: Optional[float] = None,
    prop_type: Optional[str] = None,
) -> float:
    """Probability that a player's stat exceeds the prop line.

    Uses a normal distribution centred on ``projected_value``.

    Parameters
    ----------
    projected_value:
        Model's point estimate.
    line:
        The bookmaker's prop line.
    std_dev:
        Standard deviation override.  If None, looked up from ``prop_type``.
    prop_type:
        Key into ``PROP_STD_DEVS`` (e.g. "NBA_PTS").

    Returns
    -------
    Probability in [0.01, 0.99].
    """
    if std_dev is None:
        std_dev = PROP_STD_DEVS.get(prop_type or "", _DEFAULT_STD_DEV)

    if std_dev <= 0:
        return 1.0 if projected_value > line else 0.0

    if _SCIPY_AVAILABLE:
        prob = float(_scipy_norm.sf(line, loc=projected_value, scale=std_dev))
    else:
        # Fallback: standard-normal CDF approximation
        z = (line - projected_value) / std_dev
        prob = _standard_normal_sf(z)

    return max(0.01, min(prob, 0.99))


def _standard_normal_sf(z: float) -> float:
    """Survival function of standard normal (1 - CDF) via math.erfc."""
    return 0.5 * math.erfc(z / math.sqrt(2))


# ---------------------------------------------------------------------------
# PropModel class
# ---------------------------------------------------------------------------


class PropModel:
    """Player prop projection model for a single sport and prop type."""

    def __init__(self, sport: str, prop_type: str) -> None:
        self.sport = sport.upper()
        self.prop_type = prop_type.upper()
        self._std_dev: float = PROP_STD_DEVS.get(
            f"{self.sport}_{self.prop_type}",
            PROP_STD_DEVS.get(self.prop_type, _DEFAULT_STD_DEV),
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def project_rolling_average(
        self,
        game_log: list[float],
        window: int = 10,
    ) -> float:
        """Weighted rolling average (recent games weight more)."""
        return project_rolling_average(game_log, window)

    def project_with_matchup(
        self,
        base_projection: float,
        opponent_def_rating: float,
        league_avg_def_rating: float,
    ) -> float:
        """Adjust base projection for matchup quality."""
        return project_with_matchup(
            base_projection,
            opponent_def_rating,
            league_avg_def_rating,
            self.sport,
            self.prop_type,
        )

    def over_probability(
        self,
        projected_value: float,
        line: float,
        std_dev: Optional[float] = None,
    ) -> float:
        """P(actual stat > line) under a normal distribution."""
        sd = std_dev if std_dev is not None else self._std_dev
        return over_probability(projected_value, line, sd, self.prop_type)

    def project_player_game(
        self,
        player_stats_df,  # pd.DataFrame or list[dict] with a stat column
        opponent_def_rating: float,
        league_avg_def_rating: float,
        prop_type: str,
        window: int = 10,
    ) -> dict:
        """Full player game projection.

        Parameters
        ----------
        player_stats_df:
            Either a ``pandas.DataFrame`` (with a column named after the
            stat, e.g. "PTS", "rush_yards") **or** a plain Python list of
            ``float`` values (chronological, oldest first).
        opponent_def_rating:
            Opponent defence's stat-allowed rate.
        league_avg_def_rating:
            League-average allowed rate.
        prop_type:
            Prop category key (e.g. "NBA_PTS", "NFL_RUSH_YDS").
        window:
            Number of recent games to sample.

        Returns
        -------
        dict with keys:
            projected    – point estimate
            floor        – 25th-percentile outcome (projected - 0.674*sd)
            ceiling      – 75th-percentile outcome (projected + 0.674*sd)
            confidence   – relative confidence score in [0, 1]
            games_sampled – number of games used
        """
        # --- Extract game log -----------------------------------------------
        game_log: list[float] = self._extract_game_log(
            player_stats_df, prop_type, window
        )
        games_sampled = len(game_log)

        if games_sampled == 0:
            return {
                "projected": 0.0,
                "floor": 0.0,
                "ceiling": 0.0,
                "confidence": 0.0,
                "games_sampled": 0,
            }

        # --- Base projection -------------------------------------------------
        base = project_rolling_average(game_log, window)

        # --- Matchup adjustment ----------------------------------------------
        projected = project_with_matchup(
            base,
            opponent_def_rating,
            league_avg_def_rating,
            self.sport,
            prop_type,
        )

        # --- Uncertainty bounds (±0.674 SD = 25th/75th percentile) ----------
        sd = self._std_dev
        floor_ = max(0.0, projected - 0.674 * sd)
        ceiling_ = projected + 0.674 * sd

        # --- Confidence: increases with sample size, caps at ~1.0 -----------
        # Logistic growth: reaches ~0.9 around 15 games
        confidence = 1.0 / (1.0 + math.exp(-0.4 * (games_sampled - 7)))

        return {
            "projected": round(projected, 2),
            "floor": round(floor_, 2),
            "ceiling": round(ceiling_, 2),
            "confidence": round(confidence, 4),
            "games_sampled": games_sampled,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _extract_game_log(
        self,
        player_stats_df,
        prop_type: str,
        window: int,
    ) -> list[float]:
        """Extract a numeric game log from either a DataFrame or a list."""
        if _PANDAS_AVAILABLE and isinstance(player_stats_df, pd.DataFrame):
            # Infer the column name: try the prop_type key parts
            candidates = self._column_candidates(prop_type)
            col = next(
                (c for c in candidates if c in player_stats_df.columns), None
            )
            if col is None:
                # Fallback: use first numeric column
                numeric_cols = player_stats_df.select_dtypes(include="number").columns.tolist()
                col = numeric_cols[0] if numeric_cols else None

            if col is not None:
                series = player_stats_df[col].dropna()
                return series.tail(window).tolist()
            return []

        # Plain list of floats
        if isinstance(player_stats_df, (list, tuple)):
            values = [float(v) for v in player_stats_df if v is not None]
            return values[-window:]

        return []

    @staticmethod
    def _column_candidates(prop_type: str) -> list[str]:
        """Generate possible DataFrame column names from a prop key."""
        # e.g. "NBA_PTS" → ["PTS", "pts", "points", "NBA_PTS", "nba_pts"]
        parts = prop_type.upper().split("_")
        stat_part = parts[-1] if len(parts) > 1 else prop_type
        return [
            stat_part,
            stat_part.lower(),
            prop_type,
            prop_type.lower(),
        ]
