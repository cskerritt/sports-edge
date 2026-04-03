"""
Edge detection and alert generation for prediction market contracts.

Compares model probabilities from ``GamePrediction`` records against current
market prices from ``MarketPrice`` records. When the absolute edge exceeds the
configured threshold an ``EdgeAlert`` is created (or updated if one already
exists for the contract).
"""

from __future__ import annotations

import logging
from typing import Optional

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from analytics.models import GamePrediction
from markets.kelly import kelly_from_market_price, kelly_no_position
from markets.models import ContractType, EdgeAlert, MarketContract, MarketPrice


class EdgeCalculator:
    """
    Compare ``GamePrediction`` model probabilities to ``MarketPrice`` market
    prices and generate ``EdgeAlert`` records when edge >= threshold.

    Parameters
    ----------
    edge_threshold:
        Minimum absolute edge (|model_prob - market_prob|) required to flag an
        alert. Defaults to ``settings.EDGE_ALERT_THRESHOLD`` (0.05).
    kelly_multiplier:
        Fractional Kelly multiplier for bet-sizing. Defaults to
        ``settings.DEFAULT_KELLY_FRACTION`` (0.25).
    """

    def __init__(
        self,
        edge_threshold: float | None = None,
        kelly_multiplier: float | None = None,
    ):
        self.edge_threshold = edge_threshold if edge_threshold is not None else getattr(
            settings, "EDGE_ALERT_THRESHOLD", 0.05
        )
        self.kelly_multiplier = kelly_multiplier if kelly_multiplier is not None else getattr(
            settings, "DEFAULT_KELLY_FRACTION", 0.25
        )
        self.logger = logging.getLogger("markets.edge_calculator")

    # ------------------------------------------------------------------
    # Core edge logic
    # ------------------------------------------------------------------

    def calculate_edge(self, model_prob: float, market_prob: float) -> float:
        """Edge = model_prob - market_prob.

        Positive edge means the model thinks YES is underpriced (buy YES).
        Negative edge means the model thinks NO is underpriced (buy NO).
        """
        return model_prob - market_prob

    def _model_prob_for_contract(
        self,
        prediction: GamePrediction,
        contract_type: str,
        line: float | None = None,
    ) -> float | None:
        """Extract the model probability for the YES outcome of a contract.

        Maps ``contract_type`` to the relevant probability field on
        ``GamePrediction``, with special handling for OVER/UNDER which
        requires team stats to be available.

        Parameters
        ----------
        prediction:
            The ``GamePrediction`` instance for the linked game.
        contract_type:
            One of the ``ContractType`` choices.
        line:
            Over/under line for OVER or UNDER contracts.

        Returns
        -------
        Probability in ``[0, 1]`` for YES resolving, or ``None`` if it cannot
        be determined.
        """
        if contract_type == ContractType.HOME_WIN:
            return prediction.home_win_prob

        if contract_type == ContractType.AWAY_WIN:
            return prediction.away_win_prob

        if contract_type == ContractType.DRAW:
            return prediction.draw_prob

        if contract_type in (ContractType.OVER, ContractType.UNDER):
            # Attempt to compute over probability from team season stats if
            # a line is provided. Falls back to None when stats are unavailable.
            if line is None:
                self.logger.debug(
                    "No line provided for OVER/UNDER contract — cannot compute prob."
                )
                return None

            game = prediction.game
            try:
                from analytics.over_under import TotalModel

                home_stats = game.home_team.season_stats.filter(
                    season__is_current=True
                ).first()
                away_stats = game.away_team.season_stats.filter(
                    season__is_current=True
                ).first()

                if not home_stats or not away_stats:
                    self.logger.debug(
                        "Missing season stats for game %s — cannot compute over/under prob.",
                        game,
                    )
                    return None

                model = TotalModel(game.sport)
                # Use points_per_game as pace proxy and points_allowed_per_game as defence.
                home_pace = home_stats.points_per_game or model.league_avg
                away_pace = away_stats.points_per_game or model.league_avg
                home_def = home_stats.points_allowed_per_game or model.league_avg
                away_def = away_stats.points_allowed_per_game or model.league_avg

                predicted_total = model.predict_total(home_pace, away_pace, home_def, away_def)
                over_prob = model.over_probability(predicted_total, line)

                if contract_type == ContractType.OVER:
                    return over_prob
                else:  # UNDER
                    return 1.0 - over_prob

            except Exception as exc:  # noqa: BLE001
                self.logger.warning(
                    "Could not compute OVER/UNDER prob for game %s: %s", game, exc
                )
                return None

        # PLAYER_PROP and OTHER are not supported via GamePrediction
        return None

    # ------------------------------------------------------------------
    # Single-contract processing
    # ------------------------------------------------------------------

    def process_contract(self, contract: MarketContract) -> Optional[EdgeAlert]:
        """Evaluate a single contract and create or update an EdgeAlert.

        Steps:

        1. Retrieve the most recent ``MarketPrice`` for the contract.
        2. Find the linked ``GamePrediction`` (via ``contract.game``).
        3. Extract the correct model probability for the contract type.
        4. Calculate edge.
        5. If ``|edge| >= threshold`` create or update an ``EdgeAlert``.

        Parameters
        ----------
        contract:
            The ``MarketContract`` to evaluate.

        Returns
        -------
        The ``EdgeAlert`` if one was created/updated, otherwise ``None``.
        """
        # 1. Latest market price
        try:
            market_price_obj: MarketPrice = contract.prices.latest()
        except MarketPrice.DoesNotExist:
            self.logger.debug("No market prices for contract %s — skipping.", contract)
            return None

        market_prob = market_price_obj.mid_price

        # 2. Linked game and prediction
        if not contract.game:
            self.logger.debug("Contract %s has no linked game — skipping.", contract)
            return None

        prediction: Optional[GamePrediction] = (
            GamePrediction.objects.filter(game=contract.game)
            .order_by("-created_at")
            .first()
        )
        if prediction is None:
            self.logger.debug(
                "No GamePrediction for game %s — skipping contract %s.",
                contract.game,
                contract,
            )
            return None

        # 3. Model probability
        model_prob = self._model_prob_for_contract(
            prediction, contract.contract_type, contract.line
        )
        if model_prob is None:
            return None

        # 4. Edge
        edge = self.calculate_edge(model_prob, market_prob)

        if abs(edge) < self.edge_threshold:
            self.logger.debug(
                "Contract %s edge %.4f below threshold %.4f — no alert.",
                contract,
                edge,
                self.edge_threshold,
            )
            return None

        # 5. Kelly fraction for the recommended direction
        if edge > 0:
            kf = kelly_from_market_price(model_prob, market_prob, self.kelly_multiplier)
        else:
            kf = kelly_no_position(model_prob, market_prob, self.kelly_multiplier)

        # Create or update (update edge/price snapshot if one already exists)
        with transaction.atomic():
            alert, created = EdgeAlert.objects.update_or_create(
                contract=contract,
                status="OPEN",
                defaults={
                    "market_price": market_price_obj,
                    "sport": contract.sport,
                    "model_probability": model_prob,
                    "market_probability": market_prob,
                    "edge": edge,
                    "kelly_fraction": kf,
                    "confidence": prediction.confidence,
                },
            )

        action = "Created" if created else "Updated"
        self.logger.info(
            "%s EdgeAlert for %s: edge=%.4f model=%.3f market=%.3f kelly=%.4f",
            action,
            contract,
            edge,
            model_prob,
            market_prob,
            kf,
        )

        # Send email notifications for new alerts
        if created:
            self._notify_subscribers(alert)

        return alert

    def _notify_subscribers(self, alert: EdgeAlert) -> None:
        """Send edge alert emails to Pro+ users who opted in."""
        try:
            from accounts.models import UserProfile
            from subscriptions.models import UserSubscription

            # Find users who have email_alerts enabled and are Pro+
            profiles = UserProfile.objects.filter(
                email_alerts=True,
                min_edge_alert__lte=abs(alert.edge),
            ).select_related("user")

            from sports_edge.email import send_edge_alert_email

            for profile in profiles:
                sub = getattr(profile.user, "subscription", None)
                if sub and sub.has_tier("PRO"):
                    send_edge_alert_email(profile.user, alert)
        except Exception as exc:
            self.logger.warning("Failed to send edge alert emails: %s", exc)

    # ------------------------------------------------------------------
    # Batch processing
    # ------------------------------------------------------------------

    def run_all(self) -> dict:
        """Process all active, unresolved contracts that have a linked game.

        Returns
        -------
        dict with keys:

        - ``processed``: number of contracts examined
        - ``alerts_created``: new EdgeAlert records
        - ``alerts_updated``: existing EdgeAlert records refreshed
        - ``no_edge``: contracts below threshold
        - ``errors``: contracts that raised exceptions
        """
        contracts = (
            MarketContract.objects.filter(is_active=True, is_resolved=False)
            .exclude(game__isnull=True)
            .select_related("game__home_team", "game__away_team")
        )

        processed = 0
        alerts_created = 0
        alerts_updated = 0
        no_edge = 0
        errors = 0

        existing_open = set(
            EdgeAlert.objects.filter(status="OPEN").values_list("contract_id", flat=True)
        )

        for contract in contracts:
            try:
                before_count = EdgeAlert.objects.filter(contract=contract, status="OPEN").count()
                alert = self.process_contract(contract)
                processed += 1

                if alert is None:
                    no_edge += 1
                elif contract.pk in existing_open:
                    alerts_updated += 1
                else:
                    alerts_created += 1

            except Exception as exc:  # noqa: BLE001
                self.logger.error("Error processing contract %s: %s", contract, exc)
                errors += 1

        self.logger.info(
            "run_all complete: processed=%d created=%d updated=%d no_edge=%d errors=%d",
            processed,
            alerts_created,
            alerts_updated,
            no_edge,
            errors,
        )
        return {
            "processed": processed,
            "alerts_created": alerts_created,
            "alerts_updated": alerts_updated,
            "no_edge": no_edge,
            "errors": errors,
        }

    # ------------------------------------------------------------------
    # Alert resolution
    # ------------------------------------------------------------------

    def resolve_alerts(self) -> dict:
        """Resolve OPEN EdgeAlerts whose contracts have been resolved.

        For each open alert:
        - If the contract has been resolved and ``contract.resolution`` matches
          the direction the alert was flagging (edge > 0 means YES predicted),
          mark status as ``HIT``.
        - Otherwise mark as ``MISS``.

        Returns
        -------
        dict with keys: ``resolved``, ``hits``, ``misses``.
        """
        open_alerts = (
            EdgeAlert.objects.filter(status="OPEN")
            .select_related("contract")
        )

        resolved_count = 0
        hits = 0
        misses = 0

        for alert in open_alerts:
            contract = alert.contract

            if not contract.is_resolved or contract.resolution is None:
                continue

            # Determine whether the model was right.
            # edge > 0 means the model predicted YES was more likely → resolution True = HIT.
            # edge < 0 means the model predicted NO was more likely → resolution False = HIT.
            if alert.edge > 0:
                model_was_right = contract.resolution is True
            else:
                model_was_right = contract.resolution is False

            alert.status = "HIT" if model_was_right else "MISS"
            alert.resolved_at = timezone.now()
            alert.save(update_fields=["status", "resolved_at"])

            resolved_count += 1
            if model_was_right:
                hits += 1
            else:
                misses += 1

            self.logger.info(
                "Resolved EdgeAlert %s for %s: %s",
                alert.pk,
                contract,
                alert.status,
            )

        self.logger.info(
            "resolve_alerts complete: resolved=%d hits=%d misses=%d",
            resolved_count,
            hits,
            misses,
        )
        return {"resolved": resolved_count, "hits": hits, "misses": misses}

    # ------------------------------------------------------------------
    # Leaderboard
    # ------------------------------------------------------------------

    def get_edge_leaderboard(
        self, sport: str | None = None, limit: int = 20
    ) -> list[dict]:
        """Return the top open edge alerts sorted by absolute edge descending.

        Parameters
        ----------
        sport:
            Optional sport code filter (e.g. ``"NFL"``).
        limit:
            Maximum number of results to return (default 20).

        Returns
        -------
        List of dicts, each with:

        - ``contract_id``
        - ``contract_title``
        - ``sport``
        - ``edge``
        - ``model_prob``
        - ``market_prob``
        - ``kelly_fraction``
        - ``confidence``
        """
        qs = (
            EdgeAlert.objects.filter(status="OPEN")
            .select_related("contract")
        )

        if sport:
            qs = qs.filter(sport=sport)

        # Python-side sort on abs(edge) because Django ORM doesn't support
        # abs() ordering without extra DB function imports.
        alerts = list(qs)
        alerts.sort(key=lambda a: abs(a.edge), reverse=True)
        alerts = alerts[:limit]

        return [
            {
                "contract_id": a.contract_id,
                "contract_title": a.contract.title,
                "sport": a.sport,
                "edge": a.edge,
                "model_prob": a.model_probability,
                "market_prob": a.market_probability,
                "kelly_fraction": a.kelly_fraction,
                "confidence": a.confidence,
            }
            for a in alerts
        ]
