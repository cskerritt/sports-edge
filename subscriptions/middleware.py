from .models import SubscriptionTier


class SubscriptionTierMiddleware:
    """Attach subscription_tier to every request for authenticated users."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            cached_tier = request.session.get("subscription_tier")
            if cached_tier is None:
                sub = getattr(request.user, "subscription", None)
                if sub and sub.is_active:
                    cached_tier = sub.tier
                else:
                    cached_tier = SubscriptionTier.FREE
                request.session["subscription_tier"] = cached_tier
            request.subscription_tier = cached_tier
        else:
            request.subscription_tier = SubscriptionTier.FREE

        return self.get_response(request)
