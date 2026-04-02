from django.contrib import admin
from django.http import JsonResponse
from django.urls import path, include

admin.site.site_header = "Sports Edge Admin"
admin.site.site_title = "Sports Edge"
admin.site.index_title = "Analytics Dashboard"


def healthcheck(request):
    return JsonResponse({"status": "ok"})


def landing_page(request):
    if request.user.is_authenticated:
        from django.shortcuts import redirect
        return redirect("dashboard:index")
    from django.shortcuts import render
    return render(request, "landing.html")


urlpatterns = [
    path("healthz/", healthcheck, name="healthcheck"),
    path("admin/", admin.site.urls),
    path("accounts/", include("accounts.urls")),
    path("dashboard/", include("dashboard.urls")),
    path("bankroll/", include("bankroll.urls")),
    path("markets/", include("markets.urls")),
    path("subscriptions/", include("subscriptions.urls")),
    path("", landing_page, name="landing"),
]
