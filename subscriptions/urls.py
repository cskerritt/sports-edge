from django.urls import path
from . import views

app_name = "subscriptions"

urlpatterns = [
    path("checkout/", views.checkout, name="checkout"),
    path("portal/", views.portal, name="portal"),
    path("success/", views.success, name="success"),
    path("cancel/", views.cancel, name="cancel"),
    path("pricing/", views.pricing, name="pricing"),
    path("webhook/", views.stripe_webhook, name="webhook"),
]
