from functools import wraps

from django.shortcuts import render

from .models import TIER_RANK


def requires_tier(required_tier):
    """Decorator that gates a view behind a subscription tier.

    Shows an upgrade prompt instead of a 403.
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            user_tier = getattr(request, "subscription_tier", "FREE")
            user_rank = TIER_RANK.get(user_tier, 0)
            required_rank = TIER_RANK.get(required_tier, 0)

            if user_rank < required_rank:
                return render(request, "subscriptions/upgrade_required.html", {
                    "required_tier": required_tier,
                    "current_tier": user_tier,
                }, status=403)

            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator
