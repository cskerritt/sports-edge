"""
Logistic regression win probability model for sports betting analytics.

Uses pre-set weights derived from known factors (Elo differential, rest
advantage, travel, home field).  No Django imports — pure computation only.
"""

from __future__ import annotations

import math
from typing import Optional

import numpy as np

# ---------------------------------------------------------------------------
# Pre-set model weights per sport
# Feature order: [elo_diff, rest_advantage, travel_disadvantage, intercept]
#
#   elo_diff          — home_elo - away_elo (after home advantage adjustment)
#   rest_advantage    — home_rest_days - away_rest_days (positive = home rested more)
#   travel_disadvantage — away_travel_km (large value = away team travelled far)
#   intercept         — baseline log-odds of home win
# ---------------------------------------------------------------------------

_WEIGHTS: dict[str, np.ndarray] = {
    # elo_weight, rest_weight, travel_weight, home_base
    "NFL":    np.array([0.004,  0.02, -0.00005,  0.0]),
    "NBA":    np.array([0.004,  0.02, -0.00005,  0.0]),
    "NHL":    np.array([0.004,  0.02, -0.00005,  0.0]),
    "MLB":    np.array([0.004,  0.01, -0.00003,  0.0]),
    "SOCCER": np.array([0.004,  0.01, -0.00004,  0.0]),
}

# Sport-specific calibration parameters:
#   (compress_toward_half, expansion_factor)
# compress < 1 pushes probability toward 0.5; expansion > 1 stretches it away.
_CALIBRATION: dict[str, dict] = {
    "NFL":    {"compress": 1.05, "center": 0.5},
    "NBA":    {"compress": 1.0,  "center": 0.5},
    "NHL":    {"compress": 1.0,  "center": 0.5},
    "MLB":    {"compress": 1.0,  "center": 0.5},
    "SOCCER": {"compress": 0.88, "center": 0.5},  # draws compress spread
}


def _logistic(x: float) -> float:
    """Numerically stable logistic / sigmoid function."""
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    exp_x = math.exp(x)
    return exp_x / (1.0 + exp_x)


# ---------------------------------------------------------------------------
# Feature engineering
# ---------------------------------------------------------------------------


def features_from_game_context(
    home_elo: float,
    away_elo: float,
    home_rest_days: float,
    away_rest_days: float,
    home_is_home: bool,
    travel_distance_km: float = 0.0,
) -> np.ndarray:
    """Build a feature vector for the logistic model.

    Parameters
    ----------
    home_elo, away_elo:
        Current Elo ratings (before home-advantage adjustment).
    home_rest_days, away_rest_days:
        Days since each team's last game.
    home_is_home:
        True if the "home" team is actually playing at home (always True for
        a standard game; exposed for neutral-site overrides).
    travel_distance_km:
        Estimated distance the *away* team travelled to reach the venue.

    Returns
    -------
    np.ndarray of shape (4,): [elo_diff, rest_advantage, travel_km, 1.0]
    """
    elo_diff = home_elo - away_elo
    if not home_is_home:
        elo_diff = -elo_diff  # neutral-site: no directional bias
    rest_advantage = home_rest_days - away_rest_days
    return np.array([elo_diff, rest_advantage, travel_distance_km, 1.0], dtype=float)


# ---------------------------------------------------------------------------
# Model class
# ---------------------------------------------------------------------------


class WinProbabilityModel:
    """Logistic win probability model for a single sport."""

    def __init__(self, sport: str) -> None:
        self.sport = sport.upper()
        self._weights: np.ndarray = _WEIGHTS.get(
            self.sport, _WEIGHTS["NBA"]
        ).copy()

    # ------------------------------------------------------------------
    # Core prediction
    # ------------------------------------------------------------------

    def predict(self, feature_vector: np.ndarray) -> float:
        """Return home win probability in [0, 1].

        Parameters
        ----------
        feature_vector:
            Array of shape (4,) as returned by ``features_from_game_context``.
        """
        log_odds: float = float(np.dot(self._weights, feature_vector))
        return _logistic(log_odds)

    def predict_from_context(
        self,
        home_elo: float,
        away_elo: float,
        home_rest_days: float = 3.0,
        away_rest_days: float = 3.0,
        home_is_home: bool = True,
        travel_distance_km: float = 0.0,
    ) -> float:
        """Convenience wrapper: build features and predict in one call."""
        fv = features_from_game_context(
            home_elo,
            away_elo,
            home_rest_days,
            away_rest_days,
            home_is_home,
            travel_distance_km,
        )
        return self.predict(fv)

    # ------------------------------------------------------------------
    # Calibration & blending
    # ------------------------------------------------------------------

    def calibrate(self, raw_prob: float) -> float:
        """Apply sport-specific calibration to a raw probability."""
        return calibrate_probability(raw_prob, self.sport)

    # ------------------------------------------------------------------
    # Weight access (for future fitting)
    # ------------------------------------------------------------------

    @property
    def weights(self) -> np.ndarray:
        return self._weights.copy()

    @weights.setter
    def weights(self, new_weights: np.ndarray) -> None:
        if new_weights.shape != (4,):
            raise ValueError("weights must have shape (4,)")
        self._weights = new_weights.copy()


# ---------------------------------------------------------------------------
# Module-level utility functions
# ---------------------------------------------------------------------------


def blend_predictions(
    elo_prob: float,
    logistic_prob: float,
    weights: tuple[float, float] = (0.6, 0.4),
) -> float:
    """Weighted blend of an Elo-based probability and a logistic probability.

    Parameters
    ----------
    elo_prob:
        Win probability derived from Elo ratings alone.
    logistic_prob:
        Win probability from the logistic regression model.
    weights:
        (elo_weight, logistic_weight) — must sum to 1.0 (not enforced, but
        the result will be interpreted as a probability so keep in [0,1]).

    Returns
    -------
    Blended probability clamped to [0.01, 0.99].
    """
    w_elo, w_log = weights
    blended = w_elo * elo_prob + w_log * logistic_prob
    return max(0.01, min(blended, 0.99))


def calibrate_probability(raw_prob: float, sport: str) -> float:
    """Apply sport-specific post-hoc calibration.

    Soccer has many draws, so the true win probability for either side is
    compressed toward 0.5 relative to a binary model.  NFL games tend to be
    more decisive, so we allow slight expansion.

    Parameters
    ----------
    raw_prob:
        Uncalibrated probability in [0, 1].
    sport:
        One of NFL, NBA, NHL, MLB, SOCCER (case-insensitive).

    Returns
    -------
    Calibrated probability clamped to [0.01, 0.99].
    """
    sport = sport.upper()
    params = _CALIBRATION.get(sport, {"compress": 1.0, "center": 0.5})
    compress = params["compress"]
    center = params["center"]

    # Linear compression/expansion around `center`
    calibrated = center + (raw_prob - center) * compress
    return max(0.01, min(calibrated, 0.99))
