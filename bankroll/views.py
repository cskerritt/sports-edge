import json
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Avg, Count, DecimalField, F, Q, Sum
from django.db.models.functions import Coalesce, TruncDate
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .forms import BankrollSettingsForm, BetRecordForm, SettleBetForm
from .models import BankrollSnapshot, BetOutcome, BetRecord, UserBankrollSettings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_htmx(request):
    return request.headers.get("HX-Request") == "true"


def _compute_stats(user):
    """
    Return a dict of aggregate bankroll stats for ``user``.

    Keys:
        current_balance   – from UserBankrollSettings (Decimal)
        total_pnl         – sum of all settled profit_loss (Decimal)
        roi               – total_pnl / total_wagered (float or None)
        win_rate          – settled wins / settled bets (float or None)
        total_bets        – int
        pending_bets      – int
        total_wagered     – Decimal
    """
    settings = UserBankrollSettings.get_for_user(user)
    qs = BetRecord.objects.filter(user=user)

    agg = qs.aggregate(
        total_wagered=Coalesce(Sum("amount_wagered"), Decimal("0"), output_field=DecimalField()),
        total_pnl=Coalesce(Sum("profit_loss"), Decimal("0"), output_field=DecimalField()),
        total_bets=Count("id"),
    )

    settled_qs = qs.exclude(outcome__in=[BetOutcome.PENDING, BetOutcome.VOID])
    settled_count = settled_qs.count()
    won_count = settled_qs.filter(outcome=BetOutcome.WON).count()

    win_rate = (won_count / settled_count) if settled_count else None

    total_wagered = agg["total_wagered"] or Decimal("0")
    total_pnl = agg["total_pnl"] or Decimal("0")
    roi = (float(total_pnl) / float(total_wagered)) if total_wagered else None

    pending_count = qs.filter(outcome=BetOutcome.PENDING).count()

    return {
        "current_balance": settings.current_balance,
        "total_pnl": total_pnl,
        "roi": roi,
        "win_rate": win_rate,
        "total_bets": agg["total_bets"],
        "pending_bets": pending_count,
        "total_wagered": total_wagered,
    }


def _pnl_by_sport(user):
    """
    Return a list of dicts with sport-level P&L aggregates.
    Each dict: {sport, total_wagered, total_pnl, won, lost, pending}
    """
    from sports.models import Sport

    rows = (
        BetRecord.objects
        .filter(user=user)
        .values("sport")
        .annotate(
            total_wagered=Coalesce(Sum("amount_wagered"), Decimal("0"), output_field=DecimalField()),
            total_pnl=Coalesce(Sum("profit_loss"), Decimal("0"), output_field=DecimalField()),
            won=Count("id", filter=Q(outcome=BetOutcome.WON)),
            lost=Count("id", filter=Q(outcome=BetOutcome.LOST)),
            pending=Count("id", filter=Q(outcome=BetOutcome.PENDING)),
        )
        .order_by("sport")
    )
    return list(rows)


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------

@login_required
def bankroll_index(request):
    """
    Bankroll overview page.

    Context:
        stats               – dict from _compute_stats()
        pnl_by_sport        – list of sport-level P&L dicts
        snapshots_json      – JSON array of {date, balance} for the last 30 days
        active_bets         – QuerySet of PENDING bets
        recent_settled      – last 10 settled/resolved bets
    """
    stats = _compute_stats(request.user)
    pnl_by_sport = _pnl_by_sport(request.user)

    # Last 30 bankroll snapshots for sparkline/chart data
    snapshots = (
        BankrollSnapshot.objects
        .filter(user=request.user)
        .order_by("-date")[:30]
    )
    # Reverse so chart goes oldest -> newest
    snapshots_data = [
        {
            "date": str(snap.date),
            "balance": float(snap.ending_balance),
            "pnl": float(snap.daily_pnl),
        }
        for snap in reversed(list(snapshots))
    ]
    snapshots_json = json.dumps(snapshots_data)

    active_bets = (
        BetRecord.objects
        .filter(user=request.user, outcome=BetOutcome.PENDING)
        .select_related("contract")
        .order_by("-placed_at")
    )

    recent_settled = (
        BetRecord.objects
        .filter(user=request.user)
        .exclude(outcome=BetOutcome.PENDING)
        .select_related("contract")
        .order_by("-settled_at")[:10]
    )

    context = {
        "stats": stats,
        "pnl_by_sport": pnl_by_sport,
        "snapshots_json": snapshots_json,
        "active_bets": active_bets,
        "recent_settled": recent_settled,
    }
    return render(request, "bankroll/index.html", context)


@login_required
def log_bet(request):
    """
    GET  – render a blank BetRecordForm.
    POST – validate, save the bet (assigning it to request.user), then
           redirect to bankroll_index.
    """
    if request.method == "POST":
        form = BetRecordForm(request.POST)
        if form.is_valid():
            bet = form.save(commit=False)
            bet.user = request.user
            bet.save()
            messages.success(request, f"Bet logged: {bet.description}")
            return redirect("bankroll:index")
    else:
        form = BetRecordForm()

    return render(request, "bankroll/log_bet.html", {"form": form})


@login_required
def bet_detail(request, pk):
    """
    GET  – show the full bet detail page with a SettleBetForm pre-populated.
    POST – process the SettleBetForm to settle/update the bet, then redirect
           back to the detail page.
    """
    bet = get_object_or_404(
        BetRecord.objects.select_related("contract", "contract__game"),
        pk=pk,
        user=request.user,
    )

    if request.method == "POST":
        form = SettleBetForm(request.POST, instance=bet)
        if form.is_valid():
            form.save()
            messages.success(request, "Bet updated.")
            return redirect("bankroll:bet_detail", pk=bet.pk)
    else:
        form = SettleBetForm(instance=bet)

    return render(request, "bankroll/bet_detail.html", {"bet": bet, "form": form})


@login_required
def settle_bet(request, pk):
    """
    HTMX endpoint (POST only).

    Expects ``outcome`` and ``profit_loss`` in POST data.  Validates via
    SettleBetForm, saves, and returns the updated bet-row partial so the
    caller can swap the row in-place.

    On validation error returns a 422 with the form partial so HTMX can
    display error messages.
    """
    bet = get_object_or_404(BetRecord, pk=pk, user=request.user)

    if request.method != "POST":
        return HttpResponse(status=405)

    # Auto-set settled_at if not supplied
    if not request.POST.get("settled_at"):
        post_data = request.POST.copy()
        post_data["settled_at"] = timezone.now().strftime("%Y-%m-%dT%H:%M")
    else:
        post_data = request.POST

    form = SettleBetForm(post_data, instance=bet)
    if form.is_valid():
        settled_bet = form.save()
        return render(
            request,
            "bankroll/partials/bet_row.html",
            {"bet": settled_bet},
        )

    # Return form errors so HTMX can display them inline
    return render(
        request,
        "bankroll/partials/settle_form.html",
        {"form": form, "bet": bet},
        status=422,
    )


@login_required
def bankroll_settings(request):
    """
    GET  – render BankrollSettingsForm pre-populated with the user's current
           settings (created on first access via get_or_create).
    POST – validate and save; redirect back with a success message.
    """
    settings_obj = UserBankrollSettings.get_for_user(request.user)

    if request.method == "POST":
        form = BankrollSettingsForm(request.POST, instance=settings_obj)
        if form.is_valid():
            form.save()
            messages.success(request, "Bankroll settings saved.")
            return redirect("bankroll:settings")
    else:
        form = BankrollSettingsForm(instance=settings_obj)

    return render(request, "bankroll/settings.html", {"form": form, "settings": settings_obj})


@login_required
def bet_history(request):
    """
    Paginated list of all bets (20 per page) with optional filters:
        ?sport=    – filter by sport code
        ?outcome=  – filter by BetOutcome value
        ?from=     – placed_at >= YYYY-MM-DD
        ?to=       – placed_at <= YYYY-MM-DD

    Context:
        page_obj        – paginator page
        form_data       – dict of active filter values (for re-populating the filter form)
        sport_choices   – for the sport filter select
        outcome_choices – for the outcome filter select
    """
    from sports.models import Sport

    qs = (
        BetRecord.objects
        .filter(user=request.user)
        .select_related("contract", "contract__game")
        .order_by("-placed_at")
    )

    # --- Filters ---
    sport_filter = request.GET.get("sport", "").upper()
    outcome_filter = request.GET.get("outcome", "")
    date_from = request.GET.get("from", "")
    date_to = request.GET.get("to", "")

    if sport_filter:
        qs = qs.filter(sport=sport_filter)

    if outcome_filter:
        qs = qs.filter(outcome=outcome_filter)

    if date_from:
        try:
            from datetime import date
            df = date.fromisoformat(date_from)
            qs = qs.filter(placed_at__date__gte=df)
        except ValueError:
            date_from = ""

    if date_to:
        try:
            from datetime import date
            dt = date.fromisoformat(date_to)
            qs = qs.filter(placed_at__date__lte=dt)
        except ValueError:
            date_to = ""

    # --- Summary for filtered results ---
    summary = qs.aggregate(
        total_wagered=Coalesce(Sum("amount_wagered"), Decimal("0"), output_field=DecimalField()),
        total_pnl=Coalesce(Sum("profit_loss"), Decimal("0"), output_field=DecimalField()),
        total_count=Count("id"),
        won_count=Count("id", filter=Q(outcome=BetOutcome.WON)),
        lost_count=Count("id", filter=Q(outcome=BetOutcome.LOST)),
    )

    # --- Pagination ---
    paginator = Paginator(qs, 20)
    page_number = request.GET.get("page", 1)
    page_obj = paginator.get_page(page_number)

    form_data = {
        "sport": sport_filter,
        "outcome": outcome_filter,
        "from": date_from,
        "to": date_to,
    }

    context = {
        "page_obj": page_obj,
        "form_data": form_data,
        "summary": summary,
        "sport_choices": Sport.choices,
        "outcome_choices": BetOutcome.choices,
    }
    return render(request, "bankroll/bet_history.html", context)
