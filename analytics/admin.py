from django.contrib import admin
from .models import EloRating, GamePrediction, PlayerPropProjection, BacktestResult


@admin.register(EloRating)
class EloRatingAdmin(admin.ModelAdmin):
    list_display = ("team", "rating", "date", "games_played")
    list_filter = ("team__sport",)
    search_fields = ("team__name",)
    date_hierarchy = "date"
    ordering = ("-date",)


@admin.register(GamePrediction)
class GamePredictionAdmin(admin.ModelAdmin):
    list_display = (
        "game", "model_version", "home_win_prob", "away_win_prob",
        "predicted_spread", "predicted_total", "confidence",
    )
    list_filter = ("model_version", "game__sport")
    search_fields = ("game__home_team__name", "game__away_team__name")
    date_hierarchy = "game__game_date"
    raw_id_fields = ("game",)


@admin.register(PlayerPropProjection)
class PlayerPropProjectionAdmin(admin.ModelAdmin):
    list_display = ("player", "game", "prop_type", "projected_value", "confidence")
    list_filter = ("prop_type", "player__sport")
    search_fields = ("player__name",)
    raw_id_fields = ("player", "game")


@admin.register(BacktestResult)
class BacktestResultAdmin(admin.ModelAdmin):
    list_display = ("sport", "model_version", "season", "total_games", "accuracy", "brier_score", "roi")
    list_filter = ("sport", "model_version")
