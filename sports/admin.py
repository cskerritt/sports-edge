from django.contrib import admin
from .models import League, Season, Team, Player, Game, InjuryReport, TeamSeasonStats


@admin.register(League)
class LeagueAdmin(admin.ModelAdmin):
    list_display = ("name", "abbreviation", "sport", "country")
    list_filter = ("sport",)
    search_fields = ("name", "abbreviation")


@admin.register(Season)
class SeasonAdmin(admin.ModelAdmin):
    list_display = ("label", "sport", "league", "year", "is_current", "start_date", "end_date")
    list_filter = ("sport", "is_current")
    list_editable = ("is_current",)


@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):
    list_display = ("name", "abbreviation", "sport", "conference", "division", "is_active")
    list_filter = ("sport", "conference", "is_active")
    search_fields = ("name", "abbreviation", "city")
    list_editable = ("is_active",)


@admin.register(Player)
class PlayerAdmin(admin.ModelAdmin):
    list_display = ("name", "sport", "team", "position", "is_active")
    list_filter = ("sport", "is_active", "position")
    search_fields = ("name", "first_name", "last_name")
    raw_id_fields = ("team",)


@admin.register(Game)
class GameAdmin(admin.ModelAdmin):
    list_display = ("__str__", "sport", "game_date", "status", "home_score", "away_score")
    list_filter = ("sport", "status", "game_date")
    search_fields = ("home_team__name", "away_team__name", "external_id")
    date_hierarchy = "game_date"
    raw_id_fields = ("home_team", "away_team", "season")


@admin.register(InjuryReport)
class InjuryReportAdmin(admin.ModelAdmin):
    list_display = ("player", "status", "body_part", "report_date", "game")
    list_filter = ("status", "player__sport")
    search_fields = ("player__name",)
    date_hierarchy = "report_date"


@admin.register(TeamSeasonStats)
class TeamSeasonStatsAdmin(admin.ModelAdmin):
    list_display = ("team", "season", "games_played", "wins", "losses", "points_per_game", "points_allowed_per_game")
    list_filter = ("team__sport",)
    search_fields = ("team__name",)
