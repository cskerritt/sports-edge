"""
Game context adjustments for sports betting analytics.

Rest, travel, home-field advantage, and injury impact factors.
No Django imports — pure computation only.
"""

from __future__ import annotations

import math
from typing import Optional

# ---------------------------------------------------------------------------
# Home-field advantage (win-probability boost for the home team)
# ---------------------------------------------------------------------------

_HOME_FIELD_WIN_PROB_BOOST: dict[str, float] = {
    "NFL":    0.040,   # ~57-58 % baseline home-win rate historically
    "NBA":    0.060,   # stronger home court in NBA
    "NHL":    0.040,
    "MLB":    0.035,
    "SOCCER": 0.050,
}

# ---------------------------------------------------------------------------
# Rest thresholds and adjustments
# ---------------------------------------------------------------------------

# (max_rest_days_for_tier, adjustment) — first matching tier wins
_REST_TIERS: dict[str, list[tuple[int, float]]] = {
    "NFL":    [
        (5,  -0.030),   # short week (< 6 days, e.g. Thursday game)
        (999, 0.000),
    ],
    "NBA":    [
        (1,  -0.060),   # second night of back-to-back
        (2,  -0.040),   # first night of back-to-back / one day rest
        (999, 0.000),
    ],
    "NHL":    [
        (1,  -0.060),   # same as NBA back-to-back convention
        (2,  -0.040),
        (999, 0.000),
    ],
    "MLB":    [
        (0,  -0.020),   # very rare, e.g. doubleheader fatigue
        (999, 0.000),
    ],
    "SOCCER": [
        (2,  -0.030),   # midweek fixture + weekend
        (999, 0.000),
    ],
}

# ---------------------------------------------------------------------------
# Travel
# ---------------------------------------------------------------------------

# Approximate distance proxy: one timezone hour ≈ 800 km
_KM_PER_TZ_HOUR: float = 800.0

# Beyond this many km the travel penalty caps
_TRAVEL_CAP_KM: float = 5_000.0

# Maximum penalty applied at cap distance (win-probability units)
_TRAVEL_MAX_PENALTY: dict[str, float] = {
    "NFL":    -0.020,
    "NBA":    -0.015,
    "NHL":    -0.015,
    "MLB":    -0.010,
    "SOCCER": -0.018,
}

# ---------------------------------------------------------------------------
# Injury position weights (fraction of team strength lost per player)
# ---------------------------------------------------------------------------

_POSITION_WEIGHTS: dict[str, dict[str, float]] = {
    "NFL": {
        "QB": 0.10,
        "RB": 0.03,
        "WR": 0.02,
        "TE": 0.02,
        "OL": 0.015,
        "DL": 0.015,
        "LB": 0.015,
        "DB": 0.015,
        "K":  0.005,
    },
    "NBA": {
        "PG": 0.06,
        "SG": 0.05,
        "SF": 0.05,
        "PF": 0.04,
        "C":  0.04,
    },
    "NHL": {
        "C":  0.05,
        "LW": 0.04,
        "RW": 0.04,
        "D":  0.035,
        "G":  0.08,
    },
    "MLB": {
        "SP": 0.08,   # starting pitcher
        "RP": 0.02,
        "C":  0.03,
        "1B": 0.025,
        "2B": 0.025,
        "3B": 0.025,
        "SS": 0.030,
        "OF": 0.025,
    },
    "SOCCER": {
        "GK": 0.07,
        "DEF": 0.04,
        "MID": 0.04,
        "FWD": 0.05,
    },
}

_DEFAULT_POSITION_WEIGHT: float = 0.02


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------


def rest_adjustment(rest_days: float, sport: str) -> float:
    """Return a win-probability adjustment based on rest.

    Parameters
    ----------
    rest_days:
        Days since the team's last game (0 = same day / doubleheader).
    sport:
        One of NFL, NBA, NHL, MLB, SOCCER (case-insensitive).

    Returns
    -------
    Probability delta — typically negative (fatigue penalty) or 0.0.
    """
    sport = sport.upper()
    tiers = _REST_TIERS.get(sport, [(999, 0.0)])
    rest_int = int(rest_days)
    for max_days, adjustment in tiers:
        if rest_int <= max_days:
            return adjustment
    return 0.0


def travel_distance_km(venue_tz1: str, venue_tz2: str) -> float:
    """Rough distance proxy between two venues based on timezone offset.

    Each timezone hour of difference ≈ 800 km.

    Parameters
    ----------
    venue_tz1, venue_tz2:
        IANA timezone strings (e.g. "America/New_York", "America/Los_Angeles")
        **or** raw UTC offset strings (e.g. "UTC-5", "UTC-8").

    Returns
    -------
    Estimated distance in kilometres (non-negative).
    """
    offset1 = _parse_utc_offset(venue_tz1)
    offset2 = _parse_utc_offset(venue_tz2)
    hour_diff = abs(offset1 - offset2)
    return hour_diff * _KM_PER_TZ_HOUR


def _parse_utc_offset(tz_string: str) -> float:
    """Extract a numeric UTC offset from a timezone string.

    Supports:
      - IANA names via a hard-coded mapping of common North American / European zones.
      - Raw "UTC±N" or "GMT±N" strings.
      - Plain integers (treated as hours east of UTC).
    """
    tz = tz_string.strip()

    # Fast path: numeric string
    try:
        return float(tz)
    except ValueError:
        pass

    # IANA common-zone mapping (UTC offsets, standard time)
    _IANA_OFFSETS: dict[str, float] = {
        "America/New_York":      -5.0,
        "America/Chicago":       -6.0,
        "America/Denver":        -7.0,
        "America/Phoenix":       -7.0,
        "America/Los_Angeles":   -8.0,
        "America/Anchorage":     -9.0,
        "Pacific/Honolulu":     -10.0,
        "Europe/London":          0.0,
        "Europe/Paris":           1.0,
        "Europe/Berlin":          1.0,
        "Europe/Madrid":          1.0,
        "Europe/Rome":            1.0,
        "Europe/Amsterdam":       1.0,
        "Europe/Lisbon":          0.0,
        "Europe/Moscow":          3.0,
        "Asia/Tokyo":             9.0,
        "Asia/Shanghai":          8.0,
        "Australia/Sydney":      10.0,
        "UTC":                    0.0,
    }
    if tz in _IANA_OFFSETS:
        return _IANA_OFFSETS[tz]

    # "UTC-5", "GMT+5:30", etc.
    tz_upper = tz.upper()
    for prefix in ("UTC", "GMT"):
        if tz_upper.startswith(prefix):
            remainder = tz_upper[len(prefix):].replace(":", ".").strip()
            if remainder:
                try:
                    return float(remainder)
                except ValueError:
                    pass
            return 0.0

    # Fallback — unknown timezone treated as UTC
    return 0.0


def travel_adjustment(distance_km: float, sport: str) -> float:
    """Win-probability penalty for the travelling (away) team.

    Scales linearly from 0 at 0 km to the sport's maximum penalty at
    ``_TRAVEL_CAP_KM``.  Capped beyond that distance.

    Parameters
    ----------
    distance_km:
        Estimated travel distance (from ``travel_distance_km``).
    sport:
        Sport identifier.

    Returns
    -------
    Probability adjustment (≤ 0).
    """
    sport = sport.upper()
    max_penalty = _TRAVEL_MAX_PENALTY.get(sport, -0.015)
    fraction = min(distance_km / _TRAVEL_CAP_KM, 1.0)
    return max_penalty * fraction


def home_field_advantage(sport: str) -> float:
    """Return the win-probability boost for the home team.

    Parameters
    ----------
    sport:
        Sport identifier.

    Returns
    -------
    Probability delta in [0, 1].
    """
    return _HOME_FIELD_WIN_PROB_BOOST.get(sport.upper(), 0.04)


def compute_total_adjustment(
    home_rest: float,
    away_rest: float,
    home_travel_km: float,
    away_travel_km: float,
    sport: str,
) -> dict:
    """Aggregate all context adjustments for a game.

    Parameters
    ----------
    home_rest, away_rest:
        Rest days for each team.
    home_travel_km, away_travel_km:
        Estimated travel distances (away team's distance is most relevant,
        but both are accepted for symmetry).
    sport:
        Sport identifier.

    Returns
    -------
    dict with keys:
        home_adjustment  – net win-probability delta for the home team
        away_adjustment  – net win-probability delta for the away team
        notes            – list of human-readable explanation strings
    """
    sport_upper = sport.upper()
    notes: list[str] = []

    # --- Rest adjustments ---------------------------------------------------
    home_rest_adj = rest_adjustment(home_rest, sport_upper)
    away_rest_adj = rest_adjustment(away_rest, sport_upper)

    if home_rest_adj != 0.0:
        notes.append(
            f"Home team rest penalty: {home_rest_adj:+.3f} "
            f"({int(home_rest)} days rest)"
        )
    if away_rest_adj != 0.0:
        notes.append(
            f"Away team rest penalty: {away_rest_adj:+.3f} "
            f"({int(away_rest)} days rest)"
        )

    # --- Travel adjustments -------------------------------------------------
    home_travel_adj = travel_adjustment(home_travel_km, sport_upper)
    away_travel_adj = travel_adjustment(away_travel_km, sport_upper)

    # Travel hurts the travelling team, so negate the away travel impact
    # on the home team's net adjustment (away travel helps the home side)
    if away_travel_adj != 0.0:
        notes.append(
            f"Away team travel penalty: {away_travel_adj:+.3f} "
            f"({away_travel_km:.0f} km)"
        )
    if home_travel_adj != 0.0:
        notes.append(
            f"Home team travel penalty: {home_travel_adj:+.3f} "
            f"({home_travel_km:.0f} km)"
        )

    # Home gets a positive contribution from the away team's fatigue;
    # home rarely travels to its own game so home_travel_km is typically 0.
    home_total = home_rest_adj + home_travel_adj + (-away_travel_adj * 0.5)
    away_total = away_rest_adj + away_travel_adj + (-home_travel_adj * 0.5)

    return {
        "home_adjustment": round(home_total, 4),
        "away_adjustment": round(away_total, 4),
        "notes": notes,
    }


def injury_impact_factor(
    injured_players: list[dict],
    sport: str,
) -> float:
    """Estimate team strength penalty from a list of injured players.

    Each player dict should contain at least ``"position"`` (str).
    Optionally ``"availability"`` (float in [0,1]) can override the full
    absence assumption (1.0 = fully out, 0.0 = healthy, 0.5 = limited).

    Parameters
    ----------
    injured_players:
        List of dicts, e.g.::

            [
                {"position": "QB", "availability": 0.0},
                {"position": "WR", "availability": 0.5},
            ]

    sport:
        Sport identifier.

    Returns
    -------
    Total strength penalty in [0.0, 1.0].  Multiply team's Elo or win
    probability by ``(1 - penalty)`` to apply it.
    """
    sport_upper = sport.upper()
    position_weights = _POSITION_WEIGHTS.get(sport_upper, {})

    total_penalty: float = 0.0
    for player in injured_players:
        position = str(player.get("position", "")).upper()
        weight = position_weights.get(position, _DEFAULT_POSITION_WEIGHT)

        # availability: 0.0 = out, 1.0 = healthy — we want the *absent* fraction
        availability = float(player.get("availability", 0.0))
        absence_fraction = 1.0 - max(0.0, min(availability, 1.0))

        total_penalty += weight * absence_fraction

    # Cap at 0.40 — a team doesn't lose more than 40 % of strength from injuries
    return min(total_penalty, 0.40)
