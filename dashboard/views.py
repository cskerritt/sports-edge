import json
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.db.models import Avg, Count, F, FloatField, Prefetch, Q, Sum
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from analytics.models import BacktestResult, EloRating, GamePrediction
from bankroll.models import BetOutcome, BetRecord
from markets.models import EdgeAlert, MarketContract
from sports.models import Game, GameStatus, InjuryReport, Sport


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_user_sports(request):
    """Return list of sport codes the user follows (all if none set)."""
    try:
        return request.user.profile.active_sports
    except Exception:
        return [s[0] for s in Sport.choices]


def _is_htmx(request):
    return request.headers.get("HX-Request") == "true"


def _bet_stats(user):
    """Return a dict with total_bets, win_rate, total_pnl for a user."""
    qs = BetRecord.objects.filter(user=user)
    total = qs.count()
    settled = qs.exclude(outcome=BetOutcome.PENDING).exclude(outcome=BetOutcome.VOID)
    won = settled.filter(outcome=BetOutcome.WON).count()
    settled_count = settled.count()
    win_rate = (won / settled_count) if settled_count else None
    total_pnl = qs.aggregate(pnl=Coalesce(Sum("profit_loss"), Decimal("0")))["pnl"]
    return {
        "total_bets": total,
        "win_rate": win_rate,
        "total_pnl": total_pnl,
    }


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------

@login_required
def index(request):
    """
    Main dashboard.

    Context:
        today_games   – today's Game objects filtered by the user's active sports,
                        with home_team/away_team/predictions prefetched.
        top_edges     – top 5 open EdgeAlerts for the user's sports.
        recent_bets   – the user's 5 most recent BetRecords.
        stats         – dict: total_bets, win_rate, total_pnl.
    """
    today = timezone.localdate()
    active_sports = _get_user_sports(request)

    # Prefetch the ensemble prediction for each game so we can render
    # probability tiles without N+1 queries.
    predictions_qs = GamePrediction.objects.filter(model_version="ensemble_v1")

    today_games = (
        Game.objects
        .filter(game_date=today, sport__in=active_sports)
        .exclude(status__in=[GameStatus.POSTPONED, GameStatus.CANCELLED])
        .select_related("home_team", "away_team", "season")
        .prefetch_related(
            Prefetch(
                "predictions",
                queryset=predictions_qs,
                to_attr="ensemble_predictions",
            )
        )
        .order_by("game_time")
    )

    top_edges = (
        EdgeAlert.objects
        .filter(status="OPEN", sport__in=active_sports)
        .select_related("contract", "contract__game", "market_price")
        .order_by(F("edge").desc())[:5]
    )

    recent_bets = (
        BetRecord.objects
        .filter(user=request.user)
        .select_related("contract")
        .order_by("-placed_at")[:5]
    )

    stats = _bet_stats(request.user)

    sport_emoji_map = {"NFL": "🏈", "NBA": "🏀", "NHL": "🏒", "MLB": "⚾", "SOCCER": "⚽"}
    sport_pills = [(code, sport_emoji_map.get(code, "")) for code in active_sports]

    return render(request, "dashboard/index.html", {
        "today_games": today_games,
        "today_games_count": today_games.count(),
        "open_alerts_count": top_edges.count(),
        "top_edges": top_edges,
        "recent_bets": recent_bets,
        "stats": stats,
        "sport_pills": sport_pills,
    })


@login_required
def today_games(request):
    """
    All today's games with predictions. Supports ?sport= filter.
    Returns the full page or, for HTMX requests, only the #games-container
    partial (dashboard/partials/games_list.html).
    """
    today = timezone.localdate()
    active_sports = _get_user_sports(request)
    sport_filter = request.GET.get("sport", "").upper()

    if sport_filter and sport_filter in active_sports:
        sport_qs = [sport_filter]
    else:
        sport_qs = active_sports
        sport_filter = ""

    predictions_qs = GamePrediction.objects.filter(model_version="ensemble_v1").select_related("game")

    # For edge alerts we want those attached to any contract linked to each game.
    edge_alerts_qs = (
        EdgeAlert.objects
        .filter(status="OPEN")
        .select_related("contract", "market_price")
    )

    games = (
        Game.objects
        .filter(game_date=today, sport__in=sport_qs)
        .exclude(status__in=[GameStatus.POSTPONED, GameStatus.CANCELLED])
        .select_related("home_team", "away_team", "season")
        .prefetch_related(
            Prefetch("predictions", queryset=predictions_qs, to_attr="ensemble_predictions"),
            Prefetch(
                "contracts",
                queryset=MarketContract.objects.filter(is_active=True).prefetch_related(
                    Prefetch("edge_alerts", queryset=edge_alerts_qs, to_attr="open_alerts")
                ),
                to_attr="active_contracts",
            ),
        )
        .order_by("sport", "game_time")
    )

    context = {
        "games": games,
        "today_games": games,
        "active_sports": active_sports,
        "sport_filter": sport_filter,
        "sport_choices": Sport.choices,
        "today": today,
    }

    if _is_htmx(request):
        return render(request, "dashboard/partials/games_list.html", context)
    return render(request, "dashboard/today_games.html", context)


@login_required
def edge_leaderboard(request):
    """
    Top 50 EdgeAlerts sorted by abs(edge) descending.
    Supports ?sport= and ?min_edge= (float, default 0) filters.
    """
    active_sports = _get_user_sports(request)
    sport_filter = request.GET.get("sport", "").upper()
    try:
        min_edge = float(request.GET.get("min_edge", 0))
    except (ValueError, TypeError):
        min_edge = 0.0

    qs = (
        EdgeAlert.objects
        .filter(status="OPEN", sport__in=active_sports)
        .select_related(
            "contract",
            "contract__game",
            "contract__game__home_team",
            "contract__game__away_team",
            "market_price",
        )
    )

    if sport_filter and sport_filter in active_sports:
        qs = qs.filter(sport=sport_filter)

    if min_edge:
        qs = qs.filter(Q(edge__gte=min_edge) | Q(edge__lte=-min_edge))

    # Sort by absolute edge descending using annotation
    from django.db.models.functions import Abs
    edges = qs.annotate(abs_edge_val=Abs("edge")).order_by("-abs_edge_val")[:50]

    context = {
        "edges": edges,
        "edge_alerts": edges,
        "total_edges": edges.count() if hasattr(edges, 'count') else len(edges),
        "active_sports": active_sports,
        "sport_filter": sport_filter,
        "min_edge": min_edge,
        "sport_choices": Sport.choices,
    }

    if _is_htmx(request):
        return render(request, "dashboard/partials/edge_table.html", context)
    return render(request, "dashboard/edge_leaderboard.html", context)


@login_required
def sport_detail(request, sport):
    """
    Sport-specific page showing:
    - Upcoming scheduled games for this sport
    - EloRating leaderboard (teams ranked by latest rating)
    - Most recent BacktestResult entries for this sport
    - Top open EdgeAlerts for this sport
    """
    sport = sport.upper()
    # Validate against known choices
    valid_sports = [s[0] for s in Sport.choices]
    if sport not in valid_sports:
        from django.http import Http404
        raise Http404(f"Unknown sport: {sport}")

    today = timezone.now().date()

    upcoming_games = (
        Game.objects
        .filter(sport=sport, game_date__gte=today, status=GameStatus.SCHEDULED)
        .select_related("home_team", "away_team", "season")
        .prefetch_related(
            Prefetch(
                "predictions",
                queryset=GamePrediction.objects.filter(model_version="ensemble_v1"),
                to_attr="ensemble_predictions",
            )
        )
        .order_by("game_date", "game_time")[:20]
    )

    # Elo leaderboard: get the most recent rating per team for this sport.
    # We use a subquery to find the max date per team, then fetch those rows.
    from django.db.models import OuterRef, Subquery

    latest_date_sub = (
        EloRating.objects
        .filter(team=OuterRef("team"), team__sport=sport)
        .order_by("-date")
        .values("date")[:1]
    )

    elo_leaderboard = (
        EloRating.objects
        .filter(team__sport=sport)
        .filter(date=Subquery(latest_date_sub))
        .select_related("team")
        .order_by("-rating")[:20]
    )

    backtest_results = (
        BacktestResult.objects
        .filter(sport=sport)
        .select_related("season")
        .order_by("-computed_at")[:10]
    )

    top_edges = (
        EdgeAlert.objects
        .filter(sport=sport, status="OPEN")
        .select_related("contract", "contract__game", "market_price")
        .order_by(F("edge").desc())[:10]
    )

    context = {
        "sport": sport,
        "sport_label": dict(Sport.choices).get(sport, sport),
        "upcoming_games": upcoming_games,
        "elo_leaderboard": elo_leaderboard,
        "backtest_results": backtest_results,
        "top_edges": top_edges,
    }
    return render(request, "dashboard/sport_detail.html", context)


@login_required
def backtest_results(request):
    """
    Historical model performance page.
    Groups BacktestResult entries by sport, showing accuracy, brier score, ROI.
    """
    all_results = (
        BacktestResult.objects
        .select_related("season")
        .order_by("sport", "model_version", "-computed_at")
    )

    # Build a grouped structure: {sport_code: [BacktestResult, ...]}
    grouped = {}
    for result in all_results:
        grouped.setdefault(result.sport, []).append(result)

    # Per-sport aggregate summary for the table header row
    summaries = (
        BacktestResult.objects
        .values("sport")
        .annotate(
            avg_accuracy=Avg("accuracy"),
            avg_brier=Avg("brier_score"),
            avg_roi=Avg("roi"),
            total_games=Sum("total_games"),
            version_count=Count("model_version", distinct=True),
        )
        .order_by("sport")
    )
    summary_map = {row["sport"]: row for row in summaries}

    context = {
        "grouped": grouped,
        "summary_map": summary_map,
        "sport_choices": Sport.choices,
    }
    return render(request, "dashboard/backtest_results.html", context)


@login_required
def game_detail(request, pk):
    """
    Single game detail page.
    Includes teams, ensemble prediction, all EdgeAlerts for this game's
    active contracts, injury reports for both rosters, and player prop
    projections.
    """
    game = get_object_or_404(
        Game.objects
        .select_related(
            "home_team",
            "away_team",
            "season",
        ),
        pk=pk,
    )

    predictions = (
        GamePrediction.objects
        .filter(game=game)
        .order_by("model_version")
    )

    # Contracts with prefetched open edge alerts
    contracts = (
        MarketContract.objects
        .filter(game=game)
        .prefetch_related(
            Prefetch(
                "edge_alerts",
                queryset=EdgeAlert.objects.filter(status="OPEN").select_related("market_price"),
                to_attr="open_alerts",
            ),
        )
    )

    # Injury reports for both teams (most recent report per player)
    injury_reports = (
        InjuryReport.objects
        .filter(
            player__team__in=[game.home_team, game.away_team]
        )
        .select_related("player", "player__team")
        .order_by("player__team", "-report_date")
    )

    # Player prop projections for this game
    from analytics.models import PlayerPropProjection
    prop_projections = (
        PlayerPropProjection.objects
        .filter(game=game)
        .select_related("player", "player__team")
        .order_by("player__team", "prop_type", "player__last_name")
    )

    # Group injuries and props by team for template convenience
    home_injuries = [r for r in injury_reports if r.player.team_id == game.home_team_id]
    away_injuries = [r for r in injury_reports if r.player.team_id == game.away_team_id]
    home_props = [p for p in prop_projections if p.player.team_id == game.home_team_id]
    away_props = [p for p in prop_projections if p.player.team_id == game.away_team_id]

    # Fetch latest market price for each contract in one extra query
    from markets.models import MarketPrice
    from django.db.models import Max

    contract_ids = list(contracts.values_list("id", flat=True))
    latest_price_ids = (
        MarketPrice.objects
        .filter(contract_id__in=contract_ids)
        .values("contract_id")
        .annotate(latest_id=Max("id"))
        .values_list("latest_id", flat=True)
    )
    latest_prices_map = {
        mp.contract_id: mp
        for mp in MarketPrice.objects.filter(id__in=latest_price_ids)
    }

    # Attach latest price to each contract; use prefetched open_alerts
    contracts_with_prices = []
    for contract in contracts:
        contracts_with_prices.append({
            "contract": contract,
            "latest_price": latest_prices_map.get(contract.id),
            "open_alerts": contract.open_alerts,  # populated by prefetch_related above
        })

    # Pick the ensemble prediction for the template's single-prediction display
    ensemble = predictions.filter(model_version="ensemble_v1").first()

    context = {
        "game": game,
        "prediction": ensemble,
        "predictions": predictions,
        "contracts_with_prices": contracts_with_prices,
        "home_injuries": home_injuries,
        "away_injuries": away_injuries,
        "home_props": home_props,
        "away_props": away_props,
    }
    return render(request, "dashboard/game_detail.html", context)
