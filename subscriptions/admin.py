from django.contrib import admin
from .models import UserSubscription


@admin.register(UserSubscription)
class UserSubscriptionAdmin(admin.ModelAdmin):
    list_display = ("user", "tier", "status", "current_period_end", "updated_at")
    list_filter = ("tier", "status")
    search_fields = ("user__username", "user__email", "stripe_customer_id")
    readonly_fields = ("stripe_customer_id", "stripe_subscription_id", "created_at", "updated_at")
