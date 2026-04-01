from django.urls import path

from markets import views

app_name = "markets"

urlpatterns = [
    path("", views.markets_list, name="list"),
    path("alerts/", views.edge_alerts, name="alerts"),
    path("<int:pk>/", views.contract_detail, name="contract_detail"),
]
