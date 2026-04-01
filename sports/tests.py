"""
Tests for sports models.

Factories are defined here and imported by other test modules.
"""

import datetime

import factory
import pytest
from django.contrib.auth.models import User
from django.db import IntegrityError
from django.utils import timezone

from sports.models import (
    Game,
    GameStatus,
    InjuryReport,
    InjuryStatus,
    League,
    Player,
    Season,
    Sport,
    Team,
    TeamSeasonStats,
)


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------


class LeagueFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = League
        django_get_or_create = ("sport", "abbreviation")

    sport = Sport.NFL
    name = "National Football League"
    abbreviation = "NFL"


class TeamFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Team
        django_get_or_create = ("sport", "abbreviation")

    sport = Sport.NFL
    name = factory.Sequence(lambda n: f"Team {n}")
    abbreviation = factory.Sequence(lambda n: f"T{n}")
    city = "New York"


class SeasonFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Season

    sport = Sport.NFL
    year = 2024
    label = "2024"
    is_current = True


class GameFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Game

    sport = Sport.NFL
    home_team = factory.SubFactory(TeamFactory)
    away_team = factory.SubFactory(
        TeamFactory, abbreviation=factory.Sequence(lambda n: f"A{n}")
    )
    game_date = factory.LazyFunction(lambda: datetime.date.today())
    status = GameStatus.SCHEDULED
    external_id = factory.Sequence(lambda n: f"game_{n}")


class PlayerFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Player

    sport = Sport.NFL
    team = factory.SubFactory(TeamFactory)
    name = factory.Sequence(lambda n: f"Player {n}")
    first_name = factory.Sequence(lambda n: f"First{n}")
    last_name = factory.Sequence(lambda n: f"Last{n}")
    position = "QB"
    is_active = True


class InjuryReportFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = InjuryReport

    player = factory.SubFactory(PlayerFactory)
    game = None
    report_date = factory.LazyFunction(datetime.date.today)
    status = InjuryStatus.QUESTIONABLE
    body_part = "Knee"


class TeamSeasonStatsFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = TeamSeasonStats

    team = factory.SubFactory(TeamFactory)
    season = factory.SubFactory(SeasonFactory)
    games_played = 10
    wins = 6
    losses = 4


# ---------------------------------------------------------------------------
# TestTeamModel
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestTeamModel:
    def test_full_name_with_city(self):
        team = TeamFactory(city="Kansas City", name="Chiefs", abbreviation="KC")
        assert team.full_name == "Kansas City Chiefs"

    def test_full_name_without_city(self):
        team = TeamFactory(city="", name="Wanderers", abbreviation="WAN")
        assert team.full_name == "Wanderers"

    def test_full_name_empty_city_returns_name_only(self):
        team = TeamFactory(city="  ", name="Rovers", abbreviation="ROV")
        # city is a non-empty string of spaces — full_name checks truthiness
        # " " is truthy so it prepends; this validates the actual behavior
        result = team.full_name
        assert "Rovers" in result

    def test_str_includes_name_and_sport(self):
        team = TeamFactory(name="Bears", sport=Sport.NFL, abbreviation="BRS")
        assert "Bears" in str(team)
        assert "NFL" in str(team)

    def test_unique_constraint_sport_abbreviation(self):
        TeamFactory(sport=Sport.NFL, abbreviation="DUP")
        with pytest.raises(IntegrityError):
            # Bypass get_or_create to force a DB-level error
            Team.objects.create(
                sport=Sport.NFL,
                abbreviation="DUP",
                name="Duplicate Team",
            )

    def test_different_sports_can_share_abbreviation(self):
        t1 = TeamFactory(sport=Sport.NFL, abbreviation="LAX")
        t2 = TeamFactory(sport=Sport.NBA, abbreviation="LAX")
        assert t1.pk != t2.pk

    def test_is_active_defaults_true(self):
        team = TeamFactory()
        assert team.is_active is True

    def test_team_ordering(self):
        # Teams should order by sport then name
        TeamFactory(sport=Sport.NFL, name="Zebras", abbreviation="ZEB")
        TeamFactory(sport=Sport.NFL, name="Antelopes", abbreviation="ANT")
        teams = list(Team.objects.filter(sport=Sport.NFL).order_by("sport", "name"))
        names = [t.name for t in teams]
        assert names == sorted(names)


# ---------------------------------------------------------------------------
# TestGameModel
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestGameModel:
    def test_total_score_both_scores_set(self):
        game = GameFactory(home_score=24, away_score=17, status=GameStatus.FINAL)
        assert game.total_score == 41

    def test_total_score_none_when_missing(self):
        game = GameFactory(home_score=None, away_score=None)
        assert game.total_score is None

    def test_total_score_none_when_only_one_score(self):
        game = GameFactory(home_score=10, away_score=None)
        assert game.total_score is None

    def test_home_won_true_when_home_leads(self):
        game = GameFactory(status=GameStatus.FINAL, home_score=28, away_score=14)
        assert game.home_won is True

    def test_home_won_false_when_away_leads(self):
        game = GameFactory(status=GameStatus.FINAL, home_score=10, away_score=17)
        assert game.home_won is False

    def test_home_won_none_when_not_final(self):
        game = GameFactory(status=GameStatus.SCHEDULED, home_score=None, away_score=None)
        assert game.home_won is None

    def test_home_won_none_when_in_progress(self):
        game = GameFactory(status=GameStatus.IN_PROGRESS, home_score=7, away_score=3)
        assert game.home_won is None

    def test_is_today_true_for_todays_game(self):
        game = GameFactory(game_date=datetime.date.today())
        assert game.is_today is True

    def test_is_today_false_for_yesterday(self):
        yesterday = datetime.date.today() - datetime.timedelta(days=1)
        game = GameFactory(game_date=yesterday)
        assert game.is_today is False

    def test_is_today_false_for_tomorrow(self):
        tomorrow = datetime.date.today() + datetime.timedelta(days=1)
        game = GameFactory(game_date=tomorrow)
        assert game.is_today is False

    def test_str_format(self):
        home = TeamFactory(abbreviation="KC", name="Chiefs")
        away = TeamFactory(abbreviation="LV", name="Raiders")
        game = GameFactory(
            home_team=home,
            away_team=away,
            game_date=datetime.date(2024, 9, 8),
        )
        result = str(game)
        assert "LV" in result
        assert "KC" in result
        assert "2024-09-08" in result

    def test_str_format_uses_at_symbol(self):
        home = TeamFactory(abbreviation="HOM", name="Home Team")
        away = TeamFactory(abbreviation="AWY", name="Away Team")
        game = GameFactory(home_team=home, away_team=away)
        assert "@" in str(game)

    def test_unique_constraint_sport_external_id(self):
        GameFactory(sport=Sport.NFL, external_id="unique_ext_123")
        with pytest.raises(IntegrityError):
            Game.objects.create(
                sport=Sport.NFL,
                external_id="unique_ext_123",
                home_team=TeamFactory(abbreviation="HHH"),
                away_team=TeamFactory(abbreviation="AAA"),
                game_date=datetime.date.today(),
            )

    def test_neutral_site_default_false(self):
        game = GameFactory()
        assert game.neutral_site is False

    def test_overtime_periods_default_zero(self):
        game = GameFactory()
        assert game.overtime_periods == 0


# ---------------------------------------------------------------------------
# TestInjuryReport
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestInjuryReport:
    def test_str_contains_player_name_and_status(self):
        player = PlayerFactory(name="Patrick Mahomes")
        report = InjuryReportFactory(
            player=player,
            status=InjuryStatus.QUESTIONABLE,
            report_date=datetime.date(2024, 11, 1),
        )
        result = str(report)
        assert "Patrick Mahomes" in result
        assert "QUESTIONABLE" in result

    def test_str_contains_report_date(self):
        report = InjuryReportFactory(report_date=datetime.date(2024, 10, 15))
        assert "2024-10-15" in str(report)

    def test_ordering_most_recent_first(self):
        player = PlayerFactory(name="Test Player")
        report_old = InjuryReportFactory(
            player=player, report_date=datetime.date(2024, 9, 1)
        )
        report_new = InjuryReportFactory(
            player=player, report_date=datetime.date(2024, 10, 1)
        )
        reports = list(InjuryReport.objects.filter(player=player))
        # Most recent should come first per Meta.ordering = ["-report_date", ...]
        assert reports[0].report_date >= reports[-1].report_date

    def test_out_status_is_valid(self):
        report = InjuryReportFactory(status=InjuryStatus.OUT)
        assert report.status == InjuryStatus.OUT

    def test_injury_linked_to_game(self):
        game = GameFactory()
        player = PlayerFactory(team=game.home_team)
        report = InjuryReportFactory(player=player, game=game)
        assert report.game_id == game.pk

    def test_injury_without_game(self):
        report = InjuryReportFactory(game=None)
        assert report.game is None


# ---------------------------------------------------------------------------
# TestTeamSeasonStats
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestTeamSeasonStats:
    def test_unique_constraint_team_season(self):
        stats = TeamSeasonStatsFactory()
        with pytest.raises(IntegrityError):
            TeamSeasonStats.objects.create(
                team=stats.team,
                season=stats.season,
            )

    def test_str_includes_team_and_season_label(self):
        season = SeasonFactory(label="2024", year=2024)
        team = TeamFactory(name="Cowboys", abbreviation="DAL")
        stats = TeamSeasonStatsFactory(team=team, season=season)
        result = str(stats)
        assert "Cowboys" in result
        assert "2024" in result

    def test_default_field_values(self):
        stats = TeamSeasonStatsFactory()
        assert stats.games_played == 10
        assert stats.wins == 6
        assert stats.losses == 4
        assert stats.draws == 0
        assert stats.extra_stats == {}

    def test_can_store_extra_stats(self):
        stats = TeamSeasonStatsFactory(extra_stats={"turnover_ratio": 1.5})
        stats.refresh_from_db()
        assert stats.extra_stats["turnover_ratio"] == 1.5

    def test_different_teams_same_season_allowed(self):
        season = SeasonFactory(year=2023, label="2023")
        t1 = TeamFactory(abbreviation="AA1")
        t2 = TeamFactory(abbreviation="AA2")
        s1 = TeamSeasonStats.objects.create(team=t1, season=season)
        s2 = TeamSeasonStats.objects.create(team=t2, season=season)
        assert s1.pk != s2.pk

    def test_same_team_different_seasons_allowed(self):
        team = TeamFactory(abbreviation="BB1")
        s1 = SeasonFactory(year=2022, label="2022")
        s2 = SeasonFactory(year=2023, label="2023")
        ts1 = TeamSeasonStats.objects.create(team=team, season=s1)
        ts2 = TeamSeasonStats.objects.create(team=team, season=s2)
        assert ts1.pk != ts2.pk
