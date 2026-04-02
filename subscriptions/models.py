from django.conf import settings
from django.db import models


class SubscriptionTier(models.TextChoices):
    FREE = "FREE", "Free"
    PRO = "PRO", "Pro ($19/mo)"
    ELITE = "ELITE", "Elite ($49/mo)"


TIER_RANK = {
    SubscriptionTier.FREE: 0,
    SubscriptionTier.PRO: 1,
    SubscriptionTier.ELITE: 2,
}


class SubscriptionStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    CANCELED = "canceled", "Canceled"
    PAST_DUE = "past_due", "Past Due"
    TRIALING = "trialing", "Trialing"


class UserSubscription(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="subscription",
    )
    tier = models.CharField(
        max_length=10,
        choices=SubscriptionTier.choices,
        default=SubscriptionTier.FREE,
    )
    status = models.CharField(
        max_length=20,
        choices=SubscriptionStatus.choices,
        default=SubscriptionStatus.ACTIVE,
    )
    stripe_customer_id = models.CharField(max_length=255, blank=True)
    stripe_subscription_id = models.CharField(max_length=255, blank=True)
    current_period_end = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["stripe_customer_id"]),
            models.Index(fields=["stripe_subscription_id"]),
        ]

    def __str__(self):
        return f"{self.user.username} — {self.tier}"

    @property
    def is_active(self):
        return self.status in (SubscriptionStatus.ACTIVE, SubscriptionStatus.TRIALING)

    @property
    def rank(self):
        return TIER_RANK.get(self.tier, 0)

    def has_tier(self, required_tier: str) -> bool:
        required_rank = TIER_RANK.get(required_tier, 0)
        return self.rank >= required_rank and self.is_active
