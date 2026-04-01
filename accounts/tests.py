"""
Tests for accounts models and views.
"""

import pytest
from django.contrib.auth.models import User
from django.urls import reverse

from accounts.models import UserProfile
from bankroll.models import UserBankrollSettings
from sports.models import Sport


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_user(username="acct_user", password="securepass123"):
    return User.objects.create_user(
        username=username,
        password=password,
        email=f"{username}@example.com",
    )


# ---------------------------------------------------------------------------
# TestUserProfile
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestUserProfile:
    def test_profile_created_on_user_save(self):
        user = make_user("profile_signal_user")
        assert UserProfile.objects.filter(user=user).exists()

    def test_bankroll_settings_created_on_user_save(self):
        user = make_user("bankroll_signal_user")
        assert UserBankrollSettings.objects.filter(user=user).exists()

    def test_profile_is_unique_per_user(self):
        user = make_user("unique_profile_user")
        count = UserProfile.objects.filter(user=user).count()
        assert count == 1

    def test_active_sports_returns_all_when_empty(self):
        user = make_user("all_sports_user")
        profile = user.profile
        profile.sports_followed = []
        profile.save()

        all_sport_codes = [s[0] for s in Sport.choices]
        assert sorted(profile.active_sports) == sorted(all_sport_codes)
        assert len(profile.active_sports) == 5

    def test_active_sports_returns_subset(self):
        user = make_user("subset_sports_user")
        profile = user.profile
        profile.sports_followed = ["NFL", "NBA"]
        profile.save()

        assert profile.active_sports == ["NFL", "NBA"]
        assert "NHL" not in profile.active_sports
        assert "MLB" not in profile.active_sports

    def test_active_sports_single_sport(self):
        user = make_user("single_sport_user")
        profile = user.profile
        profile.sports_followed = ["MLB"]
        profile.save()

        assert profile.active_sports == ["MLB"]

    def test_follows_sport_true_when_empty(self):
        user = make_user("follows_all_user")
        profile = user.profile
        profile.sports_followed = []
        profile.save()

        for sport_code, _ in Sport.choices:
            assert profile.follows_sport(sport_code) is True

    def test_follows_sport_respects_preferences_included(self):
        user = make_user("follows_pref_user")
        profile = user.profile
        profile.sports_followed = ["NFL", "NBA"]
        profile.save()

        assert profile.follows_sport("NFL") is True
        assert profile.follows_sport("NBA") is True

    def test_follows_sport_respects_preferences_excluded(self):
        user = make_user("follows_excluded_user")
        profile = user.profile
        profile.sports_followed = ["NFL", "NBA"]
        profile.save()

        assert profile.follows_sport("NHL") is False
        assert profile.follows_sport("MLB") is False
        assert profile.follows_sport("SOCCER") is False

    def test_str_contains_username(self):
        user = make_user("str_profile_user")
        profile = user.profile
        assert "str_profile_user" in str(profile)

    def test_profile_defaults(self):
        user = make_user("defaults_user")
        profile = user.profile
        assert profile.email_alerts is False
        assert profile.show_player_props is True
        assert profile.dark_mode is False
        assert profile.min_edge_alert == pytest.approx(0.05)


# ---------------------------------------------------------------------------
# TestAccountViews
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestAccountViews:
    # --- login ------------------------------------------------------------

    def test_login_get_200(self, client):
        response = client.get(reverse("accounts:login"))
        assert response.status_code == 200

    def test_login_get_has_form(self, client):
        response = client.get(reverse("accounts:login"))
        assert "form" in response.context

    def test_login_valid_credentials_redirects_to_dashboard(self, client):
        user = make_user("login_valid_user")
        response = client.post(
            reverse("accounts:login"),
            {"username": "login_valid_user", "password": "securepass123"},
        )
        assert response.status_code == 302
        # Should redirect to dashboard or the next param
        assert response["Location"] != reverse("accounts:login")

    def test_login_invalid_credentials_shows_error(self, client):
        make_user("login_bad_user")
        response = client.post(
            reverse("accounts:login"),
            {"username": "login_bad_user", "password": "wrongpassword"},
        )
        # Should NOT redirect — re-render the form with errors
        assert response.status_code == 200

    def test_login_invalid_username_shows_error(self, client):
        response = client.post(
            reverse("accounts:login"),
            {"username": "nobody_exists", "password": "irrelevant"},
        )
        assert response.status_code == 200

    def test_login_already_authenticated_redirects(self, client):
        user = make_user("already_auth_user")
        client.login(username="already_auth_user", password="securepass123")
        response = client.get(reverse("accounts:login"))
        # Authenticated user visiting login should be redirected away
        assert response.status_code == 302

    # --- register ---------------------------------------------------------

    def test_register_get_200(self, client):
        response = client.get(reverse("accounts:register"))
        assert response.status_code == 200

    def test_register_post_creates_user(self, client):
        data = {
            "username": "newreguser",
            "email": "newreguser@example.com",
            "password1": "ComplexP@ss123",
            "password2": "ComplexP@ss123",
        }
        before = User.objects.count()
        response = client.post(reverse("accounts:register"), data)
        after = User.objects.count()

        assert after == before + 1
        assert User.objects.filter(username="newreguser").exists()

    def test_register_creates_profile_and_bankroll_settings(self, client):
        data = {
            "username": "profilesetupuser",
            "email": "profilesetupuser@example.com",
            "password1": "ComplexP@ss123",
            "password2": "ComplexP@ss123",
        }
        client.post(reverse("accounts:register"), data)
        user = User.objects.get(username="profilesetupuser")

        assert UserProfile.objects.filter(user=user).exists()
        assert UserBankrollSettings.objects.filter(user=user).exists()

    def test_register_post_redirects_to_preferences(self, client):
        data = {
            "username": "redir_user",
            "email": "redir_user@example.com",
            "password1": "ComplexP@ss123",
            "password2": "ComplexP@ss123",
        }
        response = client.post(reverse("accounts:register"), data)
        assert response.status_code == 302
        assert reverse("accounts:preferences") in response["Location"]

    def test_register_mismatched_passwords_fails(self, client):
        data = {
            "username": "mismatch_user",
            "email": "mismatch@example.com",
            "password1": "ComplexP@ss123",
            "password2": "DifferentPass456",
        }
        before = User.objects.count()
        response = client.post(reverse("accounts:register"), data)
        after = User.objects.count()

        assert response.status_code == 200  # form redisplayed
        assert after == before  # no user created

    def test_register_duplicate_username_fails(self, client):
        make_user("dup_reg_user")
        data = {
            "username": "dup_reg_user",
            "email": "newdup@example.com",
            "password1": "ComplexP@ss123",
            "password2": "ComplexP@ss123",
        }
        before = User.objects.count()
        response = client.post(reverse("accounts:register"), data)
        after = User.objects.count()

        assert after == before
        assert response.status_code == 200

    # --- preferences ------------------------------------------------------

    def test_preferences_requires_login(self, client):
        response = client.get(reverse("accounts:preferences"))
        assert response.status_code == 302
        assert "login" in response["Location"].lower()

    def test_preferences_get_200_when_logged_in(self, client):
        user = make_user("prefs_get_user")
        client.login(username="prefs_get_user", password="securepass123")
        response = client.get(reverse("accounts:preferences"))
        assert response.status_code == 200

    def test_preferences_saves_sports_followed(self, client):
        user = make_user("prefs_save_user")
        client.login(username="prefs_save_user", password="securepass123")

        data = {
            "sports_followed": ["NFL", "NBA"],
            "email_alerts": False,
            "min_edge_alert": "0.05",
            "show_player_props": True,
            "dark_mode": False,
        }
        response = client.post(reverse("accounts:preferences"), data)

        user.profile.refresh_from_db()
        assert "NFL" in user.profile.sports_followed
        assert "NBA" in user.profile.sports_followed
        assert "NHL" not in user.profile.sports_followed

    def test_preferences_saves_dark_mode(self, client):
        user = make_user("dark_mode_user")
        client.login(username="dark_mode_user", password="securepass123")

        data = {
            "sports_followed": [],
            "email_alerts": False,
            "min_edge_alert": "0.05",
            "show_player_props": True,
            "dark_mode": True,
        }
        client.post(reverse("accounts:preferences"), data)

        user.profile.refresh_from_db()
        assert user.profile.dark_mode is True

    def test_preferences_redirect_on_success(self, client):
        user = make_user("prefs_redirect_user")
        client.login(username="prefs_redirect_user", password="securepass123")

        data = {
            "sports_followed": [],
            "email_alerts": False,
            "min_edge_alert": "0.05",
            "show_player_props": True,
            "dark_mode": False,
        }
        response = client.post(reverse("accounts:preferences"), data)
        assert response.status_code == 302

    # --- logout -----------------------------------------------------------

    def test_logout_redirects(self, client):
        user = make_user("logout_user")
        client.login(username="logout_user", password="securepass123")
        response = client.get(reverse("accounts:logout"))
        assert response.status_code == 302

    def test_logout_unauthenticates_user(self, client):
        user = make_user("logout_unauth_user")
        client.login(username="logout_unauth_user", password="securepass123")
        client.get(reverse("accounts:logout"))
        # Try to access a protected page — should redirect to login
        response = client.get(reverse("accounts:preferences"))
        assert response.status_code == 302
        assert "login" in response["Location"].lower()
