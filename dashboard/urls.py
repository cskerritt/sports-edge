from django.urls import path

from . import views

app_name = "dashboard"

urlpatterns = [
    path("", views.index, name="index"),
    path("today/", views.today_games, name="today"),
    path("edges/", views.edge_leaderboard, name="edge_leaderboard"),
    path("backtest/", views.backtest_results, name="backtest"),
    path("sport/<str:sport>/", views.sport_detail, name="sport_detail"),
    path("game/<int:pk>/", views.game_detail, name="game_detail"),
    path("refresh/", views.refresh_data, name="refresh_data"),
    path("refresh/status/", views.refresh_status, name="refresh_status"),
]
