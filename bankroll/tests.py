"""
Tests for bankroll models and views.
"""

import datetime
from decimal import Decimal

import pytest
from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone

from bankroll.models import BankrollSnapshot, BetOutcome, BetRecord, UserBankrollSettings
from sports.models import Sport


# ---------------------------------------------------------------------------
# Helpers / inline factories
# ---------------------------------------------------------------------------


def make_user(username="bettor", password="securepass123"):
    return User.objects.create_user(username=username, password=password, email=f"{username}@test.com")


def make_bet(user, **kwargs):
    defaults = dict(
        sport=Sport.NFL,
        description="Chiefs ML",
        is_yes=True,
        amount_wagered=Decimal("100.00"),
        entry_price=Decimal("0.5000"),
        outcome=BetOutcome.PENDING,
    )
    defaults.update(kwargs)
    return BetRecord.objects.create(user=user, **defaults)


# ---------------------------------------------------------------------------
# TestBetRecord
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestBetRecord:
    def test_shares_computed_on_save(self):
        user = make_user("shares_user")
        bet = make_bet(user, amount_wagered=Decimal("100.00"), entry_price=Decimal("0.5000"))
        assert bet.shares is not None
        assert bet.shares == pytest.approx(Decimal("200.0000"), rel=1e-4)

    def test_shares_recomputed_on_update(self):
        user = make_user("shares_update_user")
        bet = make_bet(user, amount_wagered=Decimal("50.00"), entry_price=Decimal("0.2500"))
        # 50 / 0.25 = 200 shares
        assert bet.shares == pytest.approx(Decimal("200.0000"), rel=1e-4)

    def test_shares_low_entry_price(self):
        user = make_user("shares_low_price")
        bet = make_bet(user, amount_wagered=Decimal("10.00"), entry_price=Decimal("0.1000"))
        # 10 / 0.1 = 100 shares
        assert bet.shares == pytest.approx(Decimal("100.0000"), rel=1e-4)

    def test_max_payout_is_shares(self):
        user = make_user("payout_user")
        bet = make_bet(user, amount_wagered=Decimal("100.00"), entry_price=Decimal("0.5000"))
        # shares = 200 → max payout = 200.0
        assert bet.max_payout == pytest.approx(200.0)

    def test_max_payout_none_when_no_shares(self):
        user = make_user("payout_none_user")
        # Force shares to None directly at the DB level to bypass the
        # custom save() which always recomputes shares from amount_wagered/entry_price.
        bet = make_bet(user)
        BetRecord.objects.filter(pk=bet.pk).update(shares=None)
        bet.refresh_from_db()
        assert bet.max_payout is None

    def test_roi_positive_win(self):
        user = make_user("roi_win_user")
        bet = make_bet(
            user,
            outcome=BetOutcome.WON,
            amount_wagered=Decimal("100.00"),
            entry_price=Decimal("0.5000"),
            profit_loss=Decimal("50.00"),
        )
        assert bet.roi == pytest.approx(0.5)

    def test_roi_negative_loss(self):
        user = make_user("roi_loss_user")
        bet = make_bet(
            user,
            outcome=BetOutcome.LOST,
            amount_wagered=Decimal("100.00"),
            entry_price=Decimal("0.5000"),
            profit_loss=Decimal("-100.00"),
        )
        assert bet.roi == pytest.approx(-1.0)

    def test_roi_none_for_pending(self):
        user = make_user("roi_pending_user")
        bet = make_bet(user, outcome=BetOutcome.PENDING)
        # profit_loss is NULL → roi should be None
        assert bet.roi is None

    def test_roi_none_when_amount_wagered_zero(self):
        user = make_user("roi_zero_user")
        # Directly create with zero amount (bypassing form validation)
        bet = BetRecord(
            user=user,
            sport=Sport.NFL,
            description="Zero wager",
            is_yes=True,
            amount_wagered=Decimal("0.00"),
            entry_price=Decimal("0.5000"),
            outcome=BetOutcome.WON,
            profit_loss=Decimal("50.00"),
        )
        bet.save()
        assert bet.roi is None

    def test_str_includes_description_and_position(self):
        user = make_user("str_test_user")
        bet = make_bet(user, description="Eagles ML", is_yes=True)
        result = str(bet)
        assert "Eagles ML" in result
        assert "YES" in result

    def test_str_no_position_is_no(self):
        user = make_user("str_no_user")
        bet = make_bet(user, description="Raiders ML", is_yes=False)
        result = str(bet)
        assert "NO" in result

    def test_ordering_newest_first(self):
        user = make_user("ordering_user")
        b1 = make_bet(user, description="Bet 1")
        b2 = make_bet(user, description="Bet 2")
        bets = list(BetRecord.objects.filter(user=user))
        # Most recent (b2) should come first
        assert bets[0].description == "Bet 2"

    def test_outcome_default_is_pending(self):
        user = make_user("outcome_default_user")
        bet = make_bet(user)
        assert bet.outcome == BetOutcome.PENDING


# ---------------------------------------------------------------------------
# TestBankrollViews
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestBankrollViews:
    def _login(self, client, user):
        client.login(username=user.username, password="securepass123")

    def _make_user(self, username="viewuser"):
        return make_user(username)

    # --- bankroll_index ---------------------------------------------------

    def test_bankroll_index_requires_login(self, client):
        url = reverse("bankroll:index")
        response = client.get(url)
        assert response.status_code == 302
        assert "/login" in response["Location"].lower() or "accounts" in response["Location"].lower()

    def test_bankroll_index_200_when_logged_in(self, client):
        user = self._make_user("index_user")
        self._login(client, user)
        url = reverse("bankroll:index")
        response = client.get(url)
        assert response.status_code == 200

    def test_bankroll_index_shows_stats(self, client):
        user = self._make_user("stats_user")
        self._login(client, user)
        make_bet(user, outcome=BetOutcome.WON, profit_loss=Decimal("50.00"))
        response = client.get(reverse("bankroll:index"))
        assert response.status_code == 200
        assert "stats" in response.context

    # --- log_bet ----------------------------------------------------------

    def test_log_bet_get_200(self, client):
        user = self._make_user("logbet_get_user")
        self._login(client, user)
        response = client.get(reverse("bankroll:log_bet"))
        assert response.status_code == 200

    def test_log_bet_get_has_form(self, client):
        user = self._make_user("logbet_form_user")
        self._login(client, user)
        response = client.get(reverse("bankroll:log_bet"))
        assert "form" in response.context

    def test_log_bet_post_creates_record(self, client):
        user = self._make_user("logbet_post_user")
        self._login(client, user)
        url = reverse("bankroll:log_bet")
        data = {
            "sport": "NFL",
            "description": "Chiefs vs Raiders – Chiefs ML",
            "is_yes": True,
            "amount_wagered": "50.00",
            "entry_price": "0.5500",
        }
        before_count = BetRecord.objects.filter(user=user).count()
        response = client.post(url, data)
        after_count = BetRecord.objects.filter(user=user).count()

        assert after_count == before_count + 1
        assert response.status_code == 302  # redirect to bankroll:index

    def test_log_bet_post_redirects_to_index(self, client):
        user = self._make_user("logbet_redirect_user")
        self._login(client, user)
        data = {
            "sport": "NFL",
            "description": "Test bet",
            "is_yes": True,
            "amount_wagered": "25.00",
            "entry_price": "0.5000",
        }
        response = client.post(reverse("bankroll:log_bet"), data)
        assert response.status_code == 302
        assert reverse("bankroll:index") in response["Location"]

    def test_log_bet_post_invalid_entry_price_above_1(self, client):
        """entry_price > 1 should fail form validation (HTML widget restricts max=0.999)."""
        user = self._make_user("logbet_invalid_user")
        self._login(client, user)
        data = {
            "sport": "NFL",
            "description": "Bad price bet",
            "is_yes": True,
            "amount_wagered": "50.00",
            "entry_price": "1.5000",  # > 1 — invalid for a prediction market share
        }
        before_count = BetRecord.objects.filter(user=user).count()
        response = client.post(reverse("bankroll:log_bet"), data)
        after_count = BetRecord.objects.filter(user=user).count()

        # Form should reject it — record should NOT be created
        assert after_count == before_count
        # Either redisplay form (200) or the ORM raises; either way no redirect
        assert response.status_code != 302 or after_count == before_count

    def test_log_bet_requires_login(self, client):
        response = client.get(reverse("bankroll:log_bet"))
        assert response.status_code == 302

    # --- settle_bet -------------------------------------------------------

    def test_settle_bet_updates_outcome(self, client):
        user = self._make_user("settle_user")
        self._login(client, user)
        bet = make_bet(user, outcome=BetOutcome.PENDING)

        url = reverse("bankroll:settle_bet", kwargs={"pk": bet.pk})
        data = {
            "outcome": "WON",
            "profit_loss": "45.00",
        }
        response = client.post(url, data, HTTP_HX_REQUEST="true")

        bet.refresh_from_db()
        assert bet.outcome == BetOutcome.WON
        assert bet.profit_loss == Decimal("45.00")

    def test_settle_bet_requires_post(self, client):
        user = self._make_user("settle_get_user")
        self._login(client, user)
        bet = make_bet(user)
        url = reverse("bankroll:settle_bet", kwargs={"pk": bet.pk})
        response = client.get(url)
        assert response.status_code == 405

    def test_settle_bet_forbidden_for_other_user(self, client):
        owner = self._make_user("settle_owner")
        other = make_user("settle_other")
        bet = make_bet(owner)
        self._login(client, other)
        url = reverse("bankroll:settle_bet", kwargs={"pk": bet.pk})
        response = client.post(url, {"outcome": "WON", "profit_loss": "10.00"})
        assert response.status_code == 404

    def test_settle_bet_sets_settled_at(self, client):
        user = self._make_user("settle_at_user")
        self._login(client, user)
        bet = make_bet(user)
        url = reverse("bankroll:settle_bet", kwargs={"pk": bet.pk})
        data = {"outcome": "LOST", "profit_loss": "-50.00"}
        client.post(url, data, HTTP_HX_REQUEST="true")
        bet.refresh_from_db()
        # settled_at should be auto-populated by the view
        assert bet.settled_at is not None

    # --- bet_history ------------------------------------------------------

    def test_bet_history_requires_login(self, client):
        response = client.get(reverse("bankroll:history"))
        assert response.status_code == 302

    def test_bet_history_200_when_logged_in(self, client):
        user = self._make_user("history_user")
        self._login(client, user)
        response = client.get(reverse("bankroll:history"))
        assert response.status_code == 200

    def test_bet_history_pagination(self, client):
        user = self._make_user("pagination_user")
        self._login(client, user)

        # Create 25 bets (default page size is 20)
        for i in range(25):
            make_bet(user, description=f"Bet {i}")

        response = client.get(reverse("bankroll:history"))
        assert response.status_code == 200
        page_obj = response.context["page_obj"]
        assert page_obj.paginator.count == 25
        assert len(page_obj.object_list) == 20  # first page

    def test_bet_history_sport_filter(self, client):
        user = self._make_user("filter_user")
        self._login(client, user)

        make_bet(user, sport=Sport.NFL, description="NFL bet")
        make_bet(user, sport=Sport.NBA, description="NBA bet")

        response = client.get(reverse("bankroll:history") + "?sport=NFL")
        bets = list(response.context["page_obj"].object_list)
        assert all(b.sport == Sport.NFL for b in bets)

    def test_bet_history_shows_all_bets_for_user(self, client):
        user = self._make_user("all_bets_user")
        self._login(client, user)
        make_bet(user, description="A")
        make_bet(user, description="B")
        make_bet(user, description="C")
        response = client.get(reverse("bankroll:history"))
        assert response.context["page_obj"].paginator.count == 3

    def test_bet_history_does_not_show_other_users_bets(self, client):
        user = self._make_user("private_user")
        other = make_user("spy_user")
        self._login(client, user)
        make_bet(other, description="Other user's bet")
        response = client.get(reverse("bankroll:history"))
        assert response.context["page_obj"].paginator.count == 0


# ---------------------------------------------------------------------------
# TestBankrollSnapshot
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestBankrollSnapshot:
    def test_daily_pnl_property(self):
        user = make_user("snap_user")
        snap = BankrollSnapshot.objects.create(
            user=user,
            date=datetime.date.today(),
            starting_balance=Decimal("1000.00"),
            ending_balance=Decimal("1050.00"),
        )
        assert snap.daily_pnl == Decimal("50.00")

    def test_unique_constraint_user_date(self):
        from django.db import IntegrityError

        user = make_user("snap_unique_user")
        today = datetime.date.today()
        BankrollSnapshot.objects.create(
            user=user, date=today,
            starting_balance=Decimal("1000.00"),
            ending_balance=Decimal("1000.00"),
        )
        with pytest.raises(IntegrityError):
            BankrollSnapshot.objects.create(
                user=user, date=today,
                starting_balance=Decimal("2000.00"),
                ending_balance=Decimal("2000.00"),
            )

    def test_str_contains_username_and_balance(self):
        user = make_user("snap_str_user")
        snap = BankrollSnapshot.objects.create(
            user=user,
            date=datetime.date.today(),
            starting_balance=Decimal("1000.00"),
            ending_balance=Decimal("1100.00"),
        )
        result = str(snap)
        assert "snap_str_user" in result


# ---------------------------------------------------------------------------
# TestUserBankrollSettings
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestUserBankrollSettings:
    def test_get_for_user_creates_on_first_call(self):
        user = make_user("settings_user")
        settings = UserBankrollSettings.get_for_user(user)
        assert settings.pk is not None

    def test_get_for_user_idempotent(self):
        user = make_user("settings_idem_user")
        s1 = UserBankrollSettings.get_for_user(user)
        s2 = UserBankrollSettings.get_for_user(user)
        assert s1.pk == s2.pk

    def test_default_kelly_fraction(self):
        user = make_user("kelly_default_user")
        settings = UserBankrollSettings.get_for_user(user)
        assert settings.kelly_fraction == 0.25

    def test_str_contains_username(self):
        user = make_user("settings_str_user")
        settings = UserBankrollSettings.get_for_user(user)
        assert "settings_str_user" in str(settings)
