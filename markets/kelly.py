"""
Kelly Criterion implementation for prediction market sizing.

All functions operate on binary markets priced between $0 and $1
(Coinbase-style prediction contracts). A YES share costs ``market_price``
and pays $1 if the event resolves YES; a NO share costs
``1 - market_price`` and pays $1 if the event resolves NO.

Fractional Kelly (``kelly_multiplier``) is applied throughout because full
Kelly is extremely aggressive in practice. The default multiplier of 0.25
(quarter-Kelly) comes from ``settings.DEFAULT_KELLY_FRACTION``, and the
maximum single-bet fraction is capped by ``settings.MAX_KELLY_FRACTION``.
"""

from __future__ import annotations

from django.conf import settings


# ---------------------------------------------------------------------------
# Core Kelly formula
# ---------------------------------------------------------------------------


def kelly_fraction(prob: float, odds_decimal: float) -> float:
    """Compute the full Kelly fraction for a binary bet.

    Formula
    -------
    ``f* = (b * p - q) / b``

    where:
    - ``b = decimal_odds - 1``  (net profit per $1 wagered)
    - ``p = prob``              (model win probability)
    - ``q = 1 - p``             (model loss probability)

    Parameters
    ----------
    prob:
        Model's estimated probability of winning (0 < p < 1).
    odds_decimal:
        Decimal odds on offer (e.g. 1.8 means a $1 bet returns $1.80).

    Returns
    -------
    Fraction of bankroll to wager in ``[0, 1]``.
    A value ≤ 0 means the bet has no positive expectation — do not bet.
    """
    if prob <= 0.0 or prob >= 1.0:
        return 0.0
    if odds_decimal <= 1.0:
        return 0.0

    b = odds_decimal - 1.0
    q = 1.0 - prob
    f = (b * prob - q) / b
    return max(f, 0.0)


# ---------------------------------------------------------------------------
# Prediction-market Kelly helpers
# ---------------------------------------------------------------------------


def kelly_from_market_price(
    model_prob: float,
    market_price: float,
    kelly_multiplier: float = None,  # type: ignore[assignment]
) -> float:
    """Kelly fraction for buying YES at ``market_price``.

    In a binary prediction market:
    - Buying YES at price ``p_mkt`` and winning pays $1 (net profit = ``1 - p_mkt``).
    - Losing costs ``p_mkt``.
    - Equivalent decimal odds: ``1 / p_mkt``  →  net-odds ``b = (1 - p_mkt) / p_mkt``.

    Applies ``kelly_multiplier`` (fractional Kelly) and clamps the result to
    ``[0, MAX_KELLY_FRACTION]`` from settings.

    Parameters
    ----------
    model_prob:
        Model's probability that YES resolves.
    market_price:
        Current YES price on the market (implied probability).
    kelly_multiplier:
        Fraction of full Kelly to use (defaults to ``settings.DEFAULT_KELLY_FRACTION``).

    Returns
    -------
    Fraction of bankroll to wager, clamped to ``[0, MAX_KELLY_FRACTION]``.
    """
    if kelly_multiplier is None:
        kelly_multiplier = getattr(settings, "DEFAULT_KELLY_FRACTION", 0.25)
    max_fraction = getattr(settings, "MAX_KELLY_FRACTION", 0.10)

    if market_price <= 0.0 or market_price >= 1.0:
        return 0.0
    if model_prob <= 0.0:
        return 0.0

    # Decimal odds for buying YES = 1 / market_price
    decimal_odds = 1.0 / market_price
    full_kelly = kelly_fraction(model_prob, decimal_odds)
    fractional = full_kelly * kelly_multiplier
    return max(0.0, min(fractional, max_fraction))


def kelly_no_position(
    model_prob: float,
    market_price: float,
    kelly_multiplier: float = None,  # type: ignore[assignment]
) -> float:
    """Kelly fraction for buying NO (shorting YES) at ``1 - market_price``.

    Buying NO at price ``(1 - market_price)`` pays $1 if the event resolves NO.

    Parameters
    ----------
    model_prob:
        Model's probability that YES resolves.
        The NO probability used internally is ``1 - model_prob``.
    market_price:
        Current YES price on the market; the implied NO price is
        ``1 - market_price``.
    kelly_multiplier:
        Fraction of full Kelly to use (defaults to ``settings.DEFAULT_KELLY_FRACTION``).

    Returns
    -------
    Fraction of bankroll to wager on NO, clamped to ``[0, MAX_KELLY_FRACTION]``.
    """
    if kelly_multiplier is None:
        kelly_multiplier = getattr(settings, "DEFAULT_KELLY_FRACTION", 0.25)
    max_fraction = getattr(settings, "MAX_KELLY_FRACTION", 0.10)

    no_model_prob = 1.0 - model_prob
    no_market_price = 1.0 - market_price

    if no_market_price <= 0.0 or no_market_price >= 1.0:
        return 0.0
    if no_model_prob <= 0.0:
        return 0.0

    decimal_odds = 1.0 / no_market_price
    full_kelly = kelly_fraction(no_model_prob, decimal_odds)
    fractional = full_kelly * kelly_multiplier
    return max(0.0, min(fractional, max_fraction))


# ---------------------------------------------------------------------------
# Expected value
# ---------------------------------------------------------------------------


def expected_value(model_prob: float, market_price: float) -> float:
    """Expected value of a $1 YES bet.

    Derivation
    ----------
    Buy 1 YES share at ``market_price``.

    - If YES resolves: receive $1, net gain = ``1 - market_price``.
    - If NO resolves: lose stake, net gain = ``-market_price``.

    ``EV = model_prob * (1 - market_price) - (1 - model_prob) * market_price``
        ``= model_prob - market_price``

    A positive EV means the model believes the contract is underpriced.

    Parameters
    ----------
    model_prob:
        Model's estimated probability for YES (0 < p < 1).
    market_price:
        Market's current YES price (implied probability).

    Returns
    -------
    Expected value per $1 stake (can be negative).
    """
    return model_prob - market_price


# ---------------------------------------------------------------------------
# Full sizing recommendation
# ---------------------------------------------------------------------------


def recommended_bet_size(
    bankroll: float,
    model_prob: float,
    market_price: float,
    kelly_multiplier: float = None,  # type: ignore[assignment]
    max_bet_fraction: float = None,  # type: ignore[assignment]
) -> dict:
    """Compute the recommended bet for a prediction-market contract.

    Evaluates both YES and NO positions and returns the one with the
    larger Kelly fraction, provided there is positive expected value.

    Parameters
    ----------
    bankroll:
        Total available bankroll in dollars.
    model_prob:
        Model's estimated probability for YES.
    market_price:
        Market's current YES price.
    kelly_multiplier:
        Fraction of full Kelly (defaults to ``settings.DEFAULT_KELLY_FRACTION``).
    max_bet_fraction:
        Hard cap on bet as fraction of bankroll
        (defaults to ``settings.MAX_KELLY_FRACTION``).

    Returns
    -------
    dict with keys:

    - ``position``: ``'YES'`` | ``'NO'`` | ``'NO_BET'``
    - ``kelly_fraction``: recommended fraction of bankroll
    - ``bet_amount``: dollar amount to wager
    - ``edge``: ``model_prob - market_price`` (positive = favour YES)
    - ``expected_value``: EV per $1 stake on the recommended position
    """
    if kelly_multiplier is None:
        kelly_multiplier = getattr(settings, "DEFAULT_KELLY_FRACTION", 0.25)
    if max_bet_fraction is None:
        max_bet_fraction = getattr(settings, "MAX_KELLY_FRACTION", 0.10)

    edge = model_prob - market_price
    yes_kelly = kelly_from_market_price(model_prob, market_price, kelly_multiplier)
    no_kelly = kelly_no_position(model_prob, market_price, kelly_multiplier)

    if yes_kelly <= 0.0 and no_kelly <= 0.0:
        return {
            "position": "NO_BET",
            "kelly_fraction": 0.0,
            "bet_amount": 0.0,
            "edge": edge,
            "expected_value": expected_value(model_prob, market_price),
        }

    if yes_kelly >= no_kelly:
        position = "YES"
        kf = yes_kelly
        ev = expected_value(model_prob, market_price)
    else:
        position = "NO"
        kf = no_kelly
        # EV of a $1 NO bet
        ev = (1.0 - model_prob) - (1.0 - market_price)

    # Double-check EV is actually positive before recommending
    if ev <= 0.0:
        return {
            "position": "NO_BET",
            "kelly_fraction": 0.0,
            "bet_amount": 0.0,
            "edge": edge,
            "expected_value": ev,
        }

    kf = min(kf, max_bet_fraction)
    bet_amount = round(bankroll * kf, 2)

    return {
        "position": position,
        "kelly_fraction": kf,
        "bet_amount": bet_amount,
        "edge": edge,
        "expected_value": ev,
    }
