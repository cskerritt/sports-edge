from django.urls import path

from . import views

app_name = "bankroll"

urlpatterns = [
    path("", views.bankroll_index, name="index"),
    path("log/", views.log_bet, name="log_bet"),
    path("history/", views.bet_history, name="history"),
    path("settings/", views.bankroll_settings, name="settings"),
    path("<int:pk>/", views.bet_detail, name="bet_detail"),
    path("<int:pk>/settle/", views.settle_bet, name="settle_bet"),
]
