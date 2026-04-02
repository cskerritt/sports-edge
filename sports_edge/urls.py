from django.contrib import admin
from django.http import JsonResponse
from django.urls import path, include

admin.site.site_header = "Sports Edge Admin"
admin.site.site_title = "Sports Edge"
admin.site.index_title = "Analytics Dashboard"


def healthcheck(request):
    return JsonResponse({"status": "ok"})


urlpatterns = [
    path("healthz/", healthcheck, name="healthcheck"),
    path("admin/", admin.site.urls),
    path("accounts/", include("accounts.urls")),
    path("dashboard/", include("dashboard.urls")),
    path("bankroll/", include("bankroll.urls")),
    path("markets/", include("markets.urls")),
    path("", include(("dashboard.urls", "dashboard"), namespace="dashboard_root")),  # root redirects to dashboard
]
