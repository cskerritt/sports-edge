"""
Views for the markets app.

All views require authentication. HTMX partial-reload responses are handled
where indicated by checking ``request.htmx`` (provided by django-htmx).
"""

from __future__ import annotations

import json

from django.contrib.auth.decorators import login_required
from django.db.models import OuterRef, Subquery, FloatField, Prefetch
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from accounts.models import UserProfile
from analytics.models import GamePrediction
from markets.models import EdgeAlert, MarketContract, MarketPrice
from sports.models import Sport


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _active_sports_for_user(user) -> list[str]:
    """Return the list of sport codes the user wants to see.

    Falls back to all sports if the user has no profile or follows nothing.
    """
    try:
        return user.profile.active_sports
    except UserProfile.DoesNotExist:
        return [code for code, _ in Sport.choices]


def _latest_price_subquery():
    """Return a Subquery that annotates a MarketContract queryset with the
    most recent ``yes_price`` for that contract."""
    return Subquery(
        MarketPrice.objects.filter(contract=OuterRef("pk"))
        .order_by("-fetched_at")
        .values("yes_price")[:1],
        output_field=FloatField(),
    )


def _mid_price_subquery():
    """Subquery for the most recent ``mid_price``."""
    return Subquery(
        MarketPrice.objects.filter(contract=OuterRef("pk"))
        .order_by("-fetched_at")
        .values("mid_price")[:1],
        output_field=FloatField(),
    )


# ---------------------------------------------------------------------------
# markets_list
# ---------------------------------------------------------------------------


@login_required
def markets_list(request):
    """Show all active MarketContracts with their latest price.

    Filtered by the authenticated user's sport preferences. Contracts are
    annotated with ``latest_yes_price`` and ``latest_mid_price`` so the
    template can display both without extra queries.

    Template: ``markets/list.html``
    HTMX partial: ``markets/_contract_rows.html``

    Query parameters
    ----------------
    sport:
        Optional sport code filter (e.g. ``?sport=NFL``).
    contract_type:
        Optional contract-type filter (e.g. ``?contract_type=HOME_WIN``).
    """
    active_sports = _active_sports_for_user(request.user)

    # Base queryset: all contracts within the user's sport prefs, sorted by
    # game date ascending (soonest first) with resolved contracts at the end.
    show_all = request.GET.get("show") == "all"
    qs = (
        MarketContract.objects.filter(sport__in=active_sports)
        .select_related("game__home_team", "game__away_team")
        .annotate(
            latest_yes_price=_latest_price_subquery(),
            latest_mid_price=_mid_price_subquery(),
        )
        .order_by("is_resolved", "game_date", "sport", "title")
    )

    if not show_all:
        qs = qs.filter(is_active=True)

    # Optional filters from query string
    sport_filter = request.GET.get("sport", "").upper()
    contract_type_filter = request.GET.get("contract_type", "")

    if sport_filter and sport_filter in dict(Sport.choices):
        qs = qs.filter(sport=sport_filter)

    if contract_type_filter:
        qs = qs.filter(contract_type=contract_type_filter)

    # Attach open EdgeAlert (if any) without an extra per-contract query
    open_alerts = {
        ea.contract_id: ea
        for ea in EdgeAlert.objects.filter(
            status="OPEN", contract__in=qs
        ).select_related("contract")
    }

    contracts = list(qs)
    for contract in contracts:
        contract.open_alert = open_alerts.get(contract.pk)

    context = {
        "contracts": contracts,
        "sport_choices": Sport.choices,
        "active_sports": active_sports,
        "selected_sport": sport_filter,
        "selected_contract_type": contract_type_filter,
        "contract_type_choices": MarketContract._meta.get_field("contract_type").choices,
        "total_count": len(contracts),
        "show_all": show_all,
    }

    if request.htmx:
        return render(request, "markets/_contract_rows.html", context)

    return render(request, "markets/list.html", context)


# ---------------------------------------------------------------------------
# edge_alerts
# ---------------------------------------------------------------------------


@login_required
def edge_alerts(request):
    """Display open EdgeAlerts ordered by absolute edge descending.

    Supports filtering by sport via ``?sport=``. When the request comes from
    HTMX (e.g. a polling swap or sport-filter click) the partial template
    ``markets/_alerts_table.html`` is returned instead of the full page.

    Template: ``markets/alerts.html``
    HTMX partial: ``markets/_alerts_table.html``

    Query parameters
    ----------------
    sport:
        Optional sport code filter.
    min_edge:
        Optional minimum absolute edge filter (float, e.g. ``0.08``).
    """
    active_sports = _active_sports_for_user(request.user)

    qs = (
        EdgeAlert.objects.filter(status="OPEN", sport__in=active_sports)
        .select_related(
            "contract",
            "contract__game",
            "contract__game__home_team",
            "contract__game__away_team",
            "market_price",
        )
        .order_by("-edge", "created_at")  # most positive edge first
    )

    sport_filter = request.GET.get("sport", "").upper()
    if sport_filter and sport_filter in dict(Sport.choices):
        qs = qs.filter(sport=sport_filter)

    try:
        min_edge = float(request.GET.get("min_edge", 0))
    except (TypeError, ValueError):
        min_edge = 0.0

    # Retrieve all and sort by abs(edge) descending
    alerts = list(qs)
    if min_edge > 0:
        alerts = [a for a in alerts if abs(a.edge) >= min_edge]
    alerts.sort(key=lambda a: abs(a.edge), reverse=True)

    # Sport summary for sidebar / filter pill counts
    sport_counts: dict[str, int] = {}
    for alert in EdgeAlert.objects.filter(status="OPEN", sport__in=active_sports).values("sport"):
        sport_counts[alert["sport"]] = sport_counts.get(alert["sport"], 0) + 1

    context = {
        "alerts": alerts,
        "open_alerts": alerts,
        "sport_choices": Sport.choices,
        "active_sports": active_sports,
        "selected_sport": sport_filter,
        "min_edge": min_edge,
        "sport_counts": sport_counts,
        "total_alerts": len(alerts),
        "last_updated": timezone.now(),
    }

    if request.htmx:
        return render(request, "markets/_alerts_table.html", context)

    return render(request, "markets/alerts.html", context)


# ---------------------------------------------------------------------------
# contract_detail
# ---------------------------------------------------------------------------


@login_required
def contract_detail(request, pk: int):
    """Show detail for a single MarketContract.

    Includes:
    - The last 24 price snapshots serialised as JSON for a price-history
      chart (``price_history_json``).
    - The linked ``GamePrediction`` (latest model version).
    - The open ``EdgeAlert`` for this contract, if one exists.

    Template: ``markets/contract_detail.html``
    """
    contract = get_object_or_404(
        MarketContract.objects.select_related(
            "game__home_team",
            "game__away_team",
            "game__season",
        ),
        pk=pk,
    )

    # Last 24 price snapshots, oldest-first for charting
    recent_prices = list(
        contract.prices.order_by("-fetched_at").select_related()[:24]
    )
    recent_prices.reverse()  # chronological order for the chart

    price_history = [
        {
            "timestamp": p.fetched_at.isoformat(),
            "yes_price": round(p.yes_price, 4),
            "no_price": round(p.no_price, 4),
            "mid_price": round(p.mid_price, 4),
        }
        for p in recent_prices
    ]
    price_history_json = json.dumps(price_history)

    # Latest price snapshot
    latest_price = recent_prices[-1] if recent_prices else None

    # Linked prediction (most recent model run)
    prediction: GamePrediction | None = None
    if contract.game:
        prediction = (
            GamePrediction.objects.filter(game=contract.game)
            .select_related("game__home_team", "game__away_team")
            .order_by("-created_at")
            .first()
        )

    # Open EdgeAlert
    open_alert: EdgeAlert | None = (
        EdgeAlert.objects.filter(contract=contract, status="OPEN")
        .select_related("market_price")
        .first()
    )

    # Historical alerts (resolved), most recent first
    resolved_alerts = (
        EdgeAlert.objects.filter(contract=contract)
        .exclude(status="OPEN")
        .order_by("-resolved_at")[:10]
    )

    context = {
        "contract": contract,
        "latest_price": latest_price,
        "price_history_json": price_history_json,
        "price_history": recent_prices,
        "prediction": prediction,
        "open_alert": open_alert,
        "resolved_alerts": resolved_alerts,
        "has_price_data": bool(recent_prices),
    }

    return render(request, "markets/contract_detail.html", context)
