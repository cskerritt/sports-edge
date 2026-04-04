# Sports Edge Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix NBA game display, add Stripe tiered subscriptions (Free/Pro/Elite), gate dashboard features by tier, and polish the UI with a public landing page.

**Architecture:** New `subscriptions` Django app with Stripe Checkout + Webhooks + Customer Portal. Middleware attaches tier to every request. Template tags gate UI elements. Landing page is public; dashboard requires login. Dark theme already exists — we enhance it with tier badges and upgrade prompts.

**Tech Stack:** Django 5.1, Stripe Python SDK, HTMX, Tailwind (CDN), Alpine.js

---

## File Structure

### New Files
```
subscriptions/                         # New Django app
  __init__.py
  apps.py
  models.py                            # SubscriptionTier enum, UserSubscription model
  views.py                             # checkout, webhook, portal, success, cancel
  urls.py                              # /subscriptions/ routes
  middleware.py                        # SubscriptionTierMiddleware
  decorators.py                        # requires_tier view decorator
  signals.py                           # Auto-create UserSubscription on User create
  templatetags/
    __init__.py
    subscription_tags.py               # if_tier / endif_tier template tags
  migrations/
    __init__.py
  templates/
    subscriptions/
      upgrade_required.html            # Full-page upgrade prompt
      success.html                     # Post-checkout success
      cancel.html                      # Post-checkout cancel

sports/management/commands/
  refresh_today.py                     # Force-refresh today's games

templates/
  landing.html                         # Public landing/pricing page
  dashboard/
    partials/
      upgrade_prompt.html              # Inline upgrade CTA partial
```

### Modified Files
```
requirements.txt                       # Add stripe
sports_edge/settings/base.py           # Add subscriptions app, Stripe settings, middleware
sports_edge/urls.py                    # Add subscriptions URLs, landing page route
dashboard/views.py                     # Timezone fix, tier gating on views
templates/base.html                    # Tier badge in nav, subscription link in user menu
templates/dashboard/index.html         # Tier-gated predictions/edges sections
templates/dashboard/partials/games_list.html  # Tier-gated prediction columns
templates/dashboard/game_detail.html   # Tier-gated props/predictions
templates/dashboard/edge_leaderboard.html     # Pro+ gate
templates/dashboard/backtest_results.html     # Elite gate
templates/dashboard/sport_detail.html  # Mixed tier gating
```

---

## Task 1: Fix NBA Game Display — Timezone Handling

**Files:**
- Modify: `dashboard/views.py:53-84` (index view)
- Modify: `dashboard/views.py:117-171` (today_games view)
- Modify: `sports/ingestion/base.py:143-147` (_today method)

- [ ] **Step 1: Fix `_today()` in base ingestor to use project timezone**

In `sports/ingestion/base.py`, replace the `_today` method:

```python
@staticmethod
def _today() -> date:
    """Return today's date in the project timezone (America/New_York)."""
    from django.utils import timezone
    return timezone.localdate()
```

- [ ] **Step 2: Fix dashboard views to use timezone-aware date**

In `dashboard/views.py`, update the `index` view (line 66):

```python
# Replace:
today = timezone.now().date()
# With:
today = timezone.localdate()
```

Do the same in the `today_games` view (line 123):

```python
# Replace:
today = timezone.now().date()
# With:
today = timezone.localdate()
```

- [ ] **Step 3: Commit**

```bash
git add dashboard/views.py sports/ingestion/base.py
git commit -m "fix: use timezone-aware localdate() for NBA game display"
```

---

## Task 2: Add refresh_today Management Command

**Files:**
- Create: `sports/management/commands/refresh_today.py`

- [ ] **Step 1: Create the management command**

```python
"""Force-refresh today's games from ESPN for all sports."""
import logging
from django.core.management.base import BaseCommand
from django.utils import timezone

from sports.ingestion.nba import NBAIngestor
from sports.ingestion.nfl import NFLIngestor
from sports.ingestion.nhl import NHLIngestor
from sports.ingestion.mlb import MLBIngestor

logger = logging.getLogger(__name__)

INGESTORS = [
    NBAIngestor,
    NFLIngestor,
    NHLIngestor,
    MLBIngestor,
]


class Command(BaseCommand):
    help = "Force-refresh today's games from ESPN scoreboard for all sports."

    def add_arguments(self, parser):
        parser.add_argument(
            "--sport",
            type=str,
            help="Only refresh a specific sport (NBA, NFL, NHL, MLB)",
        )

    def handle(self, *args, **options):
        today = timezone.localdate()
        sport_filter = (options.get("sport") or "").upper()

        for ingestor_cls in INGESTORS:
            if sport_filter and ingestor_cls.sport != sport_filter:
                continue

            self.stdout.write(f"Refreshing {ingestor_cls.sport} for {today}...")
            try:
                ingestor = ingestor_cls()
                # Ensure teams have ESPN IDs first
                ingestor.ingest_teams()
                result = ingestor.ingest_scores(game_date=today)
                self.stdout.write(
                    self.style.SUCCESS(
                        f"  {ingestor_cls.sport}: created={result['created']} "
                        f"updated={result['updated']} errors={result['errors']}"
                    )
                )
            except Exception as exc:
                self.stdout.write(self.style.ERROR(f"  {ingestor_cls.sport}: {exc}"))

        self.stdout.write(self.style.SUCCESS("Done."))
```

- [ ] **Step 2: Verify the command directory exists**

```bash
ls sports/management/commands/
```

- [ ] **Step 3: Commit**

```bash
git add sports/management/commands/refresh_today.py
git commit -m "feat: add refresh_today management command for ESPN game sync"
```

---

## Task 3: Stripe Package & Settings

**Files:**
- Modify: `requirements.txt`
- Modify: `sports_edge/settings/base.py`

- [ ] **Step 1: Add stripe to requirements.txt**

Append to `requirements.txt`:

```
# Payments
stripe>=8.0.0
```

- [ ] **Step 2: Add Stripe settings to base.py**

Append after the edge detection settings block (after line 107):

```python
# Stripe payments
STRIPE_SECRET_KEY = env("STRIPE_SECRET_KEY", default="")
STRIPE_PUBLISHABLE_KEY = env("STRIPE_PUBLISHABLE_KEY", default="")
STRIPE_WEBHOOK_SECRET = env("STRIPE_WEBHOOK_SECRET", default="")
STRIPE_PRO_PRICE_ID = env("STRIPE_PRO_PRICE_ID", default="")
STRIPE_ELITE_PRICE_ID = env("STRIPE_ELITE_PRICE_ID", default="")
```

- [ ] **Step 3: Add subscriptions to INSTALLED_APPS**

In `sports_edge/settings/base.py`, add `"subscriptions"` to INSTALLED_APPS after `"bankroll"`:

```python
INSTALLED_APPS = [
    ...
    "bankroll",
    "subscriptions",
]
```

- [ ] **Step 4: Commit**

```bash
git add requirements.txt sports_edge/settings/base.py
git commit -m "feat: add stripe package and settings for subscription system"
```

---

## Task 4: Subscriptions App — Models & Signals

**Files:**
- Create: `subscriptions/__init__.py`
- Create: `subscriptions/apps.py`
- Create: `subscriptions/models.py`
- Create: `subscriptions/signals.py`
- Create: `subscriptions/admin.py`
- Create: `subscriptions/migrations/__init__.py`

- [ ] **Step 1: Create the app scaffold**

`subscriptions/__init__.py`:
```python
```

`subscriptions/apps.py`:
```python
from django.apps import AppConfig


class SubscriptionsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "subscriptions"

    def ready(self):
        import subscriptions.signals  # noqa: F401
```

`subscriptions/migrations/__init__.py`:
```python
```

- [ ] **Step 2: Create models**

`subscriptions/models.py`:
```python
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
        """Return True if user's tier meets or exceeds required_tier."""
        required_rank = TIER_RANK.get(required_tier, 0)
        return self.rank >= required_rank and self.is_active
```

- [ ] **Step 3: Create signals**

`subscriptions/signals.py`:
```python
from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import UserSubscription

User = get_user_model()


@receiver(post_save, sender=User)
def create_user_subscription(sender, instance, created, **kwargs):
    if created:
        UserSubscription.objects.get_or_create(user=instance)
```

- [ ] **Step 4: Create admin**

`subscriptions/admin.py`:
```python
from django.contrib import admin
from .models import UserSubscription


@admin.register(UserSubscription)
class UserSubscriptionAdmin(admin.ModelAdmin):
    list_display = ("user", "tier", "status", "current_period_end", "updated_at")
    list_filter = ("tier", "status")
    search_fields = ("user__username", "user__email", "stripe_customer_id")
    readonly_fields = ("stripe_customer_id", "stripe_subscription_id", "created_at", "updated_at")
```

- [ ] **Step 5: Generate and run migration**

```bash
python manage.py makemigrations subscriptions
python manage.py migrate
```

- [ ] **Step 6: Commit**

```bash
git add subscriptions/
git commit -m "feat: add subscriptions app with UserSubscription model and signals"
```

---

## Task 5: Subscriptions — Middleware & Decorators

**Files:**
- Create: `subscriptions/middleware.py`
- Create: `subscriptions/decorators.py`
- Modify: `sports_edge/settings/base.py` (MIDDLEWARE list)

- [ ] **Step 1: Create middleware**

`subscriptions/middleware.py`:
```python
from .models import SubscriptionTier


class SubscriptionTierMiddleware:
    """Attach subscription_tier to every request for authenticated users."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            # Cache in session to avoid DB hit every request
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
```

- [ ] **Step 2: Create decorator**

`subscriptions/decorators.py`:
```python
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
```

- [ ] **Step 3: Add middleware to settings**

In `sports_edge/settings/base.py`, add to MIDDLEWARE list after `HtmxMiddleware`:

```python
"subscriptions.middleware.SubscriptionTierMiddleware",
```

- [ ] **Step 4: Commit**

```bash
git add subscriptions/middleware.py subscriptions/decorators.py sports_edge/settings/base.py
git commit -m "feat: add subscription tier middleware and requires_tier decorator"
```

---

## Task 6: Subscriptions — Template Tags

**Files:**
- Create: `subscriptions/templatetags/__init__.py`
- Create: `subscriptions/templatetags/subscription_tags.py`

- [ ] **Step 1: Create template tag directory**

`subscriptions/templatetags/__init__.py`:
```python
```

- [ ] **Step 2: Create template tags**

`subscriptions/templatetags/subscription_tags.py`:
```python
from django import template

from subscriptions.models import TIER_RANK

register = template.Library()


class IfTierNode(template.Node):
    def __init__(self, required_tier, nodelist_true, nodelist_false):
        self.required_tier = required_tier
        self.nodelist_true = nodelist_true
        self.nodelist_false = nodelist_false

    def render(self, context):
        request = context.get("request")
        user_tier = getattr(request, "subscription_tier", "FREE") if request else "FREE"
        user_rank = TIER_RANK.get(user_tier, 0)
        required_rank = TIER_RANK.get(self.required_tier, 0)

        if user_rank >= required_rank:
            return self.nodelist_true.render(context)
        return self.nodelist_false.render(context)


@register.tag("if_tier")
def do_if_tier(parser, token):
    """Usage: {% if_tier "PRO" %} ... {% else_tier %} ... {% endif_tier %}"""
    bits = token.split_contents()
    if len(bits) != 2:
        raise template.TemplateSyntaxError("if_tier requires one argument: the tier name")

    required_tier = bits[1].strip('"').strip("'")

    nodelist_true = parser.parse(("else_tier", "endif_tier"))
    token = parser.next_token()
    if token.contents == "else_tier":
        nodelist_false = parser.parse(("endif_tier",))
        parser.delete_first_token()
    else:
        nodelist_false = template.NodeList()

    return IfTierNode(required_tier, nodelist_true, nodelist_false)


@register.simple_tag(takes_context=True)
def user_tier(context):
    """Return the current user's subscription tier string."""
    request = context.get("request")
    return getattr(request, "subscription_tier", "FREE") if request else "FREE"


@register.simple_tag(takes_context=True)
def tier_badge_class(context):
    """Return Tailwind classes for the user's tier badge."""
    request = context.get("request")
    tier = getattr(request, "subscription_tier", "FREE") if request else "FREE"
    return {
        "FREE": "bg-slate-700 text-slate-300",
        "PRO": "bg-blue-600 text-white",
        "ELITE": "bg-purple-600 text-white",
    }.get(tier, "bg-slate-700 text-slate-300")
```

- [ ] **Step 3: Commit**

```bash
git add subscriptions/templatetags/
git commit -m "feat: add if_tier template tag and tier badge helpers"
```

---

## Task 7: Subscriptions — Views & URLs (Stripe Checkout + Webhook)

**Files:**
- Create: `subscriptions/views.py`
- Create: `subscriptions/urls.py`
- Modify: `sports_edge/urls.py`

- [ ] **Step 1: Create views**

`subscriptions/views.py`:
```python
import stripe
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .models import SubscriptionTier, UserSubscription

stripe.api_key = settings.STRIPE_SECRET_KEY

TIER_PRICE_MAP = {
    SubscriptionTier.PRO: "STRIPE_PRO_PRICE_ID",
    SubscriptionTier.ELITE: "STRIPE_ELITE_PRICE_ID",
}


def _get_or_create_stripe_customer(user):
    """Get or create a Stripe customer for the given user."""
    sub = user.subscription
    if sub.stripe_customer_id:
        return sub.stripe_customer_id

    customer = stripe.Customer.create(
        email=user.email,
        name=user.get_full_name() or user.username,
        metadata={"user_id": str(user.pk)},
    )
    sub.stripe_customer_id = customer.id
    sub.save(update_fields=["stripe_customer_id", "updated_at"])
    return customer.id


@login_required
def checkout(request):
    """Create a Stripe Checkout session and redirect."""
    tier = request.GET.get("tier", "").upper()
    price_setting = TIER_PRICE_MAP.get(tier)
    if not price_setting:
        return redirect("subscriptions:pricing")

    price_id = getattr(settings, price_setting, "")
    if not price_id:
        return render(request, "subscriptions/upgrade_required.html", {
            "error": "Stripe is not configured yet. Please set up price IDs.",
            "required_tier": tier,
            "current_tier": getattr(request, "subscription_tier", "FREE"),
        })

    customer_id = _get_or_create_stripe_customer(request.user)

    session = stripe.checkout.Session.create(
        customer=customer_id,
        payment_method_types=["card"],
        line_items=[{"price": price_id, "quantity": 1}],
        mode="subscription",
        success_url=request.build_absolute_uri("/subscriptions/success/"),
        cancel_url=request.build_absolute_uri("/subscriptions/cancel/"),
        metadata={"user_id": str(request.user.pk), "tier": tier},
    )
    return redirect(session.url, code=303)


@login_required
def portal(request):
    """Redirect to Stripe Customer Portal for subscription management."""
    sub = request.user.subscription
    if not sub.stripe_customer_id:
        return redirect("subscriptions:pricing")

    session = stripe.billing_portal.Session.create(
        customer=sub.stripe_customer_id,
        return_url=request.build_absolute_uri("/dashboard/"),
    )
    return redirect(session.url, code=303)


@login_required
def success(request):
    """Post-checkout success page."""
    # Invalidate cached tier so middleware re-reads from DB
    request.session.pop("subscription_tier", None)
    return render(request, "subscriptions/success.html")


@login_required
def cancel(request):
    """Post-checkout cancellation page."""
    return render(request, "subscriptions/cancel.html")


def pricing(request):
    """Public pricing page."""
    return render(request, "landing.html", {"show_pricing": True})


@csrf_exempt
@require_POST
def stripe_webhook(request):
    """Handle Stripe webhook events."""
    payload = request.body
    sig_header = request.META.get("HTTP_STRIPE_SIGNATURE", "")

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, settings.STRIPE_WEBHOOK_SECRET
        )
    except (ValueError, stripe.error.SignatureVerificationError):
        return HttpResponse(status=400)

    event_type = event["type"]
    data = event["data"]["object"]

    if event_type == "checkout.session.completed":
        _handle_checkout_completed(data)
    elif event_type == "customer.subscription.updated":
        _handle_subscription_updated(data)
    elif event_type == "customer.subscription.deleted":
        _handle_subscription_deleted(data)
    elif event_type == "invoice.payment_failed":
        _handle_payment_failed(data)

    return HttpResponse(status=200)


def _handle_checkout_completed(session):
    """Process successful checkout — activate subscription."""
    customer_id = session.get("customer", "")
    subscription_id = session.get("subscription", "")
    tier = session.get("metadata", {}).get("tier", "PRO")

    try:
        sub = UserSubscription.objects.get(stripe_customer_id=customer_id)
    except UserSubscription.DoesNotExist:
        user_id = session.get("metadata", {}).get("user_id")
        if user_id:
            sub, _ = UserSubscription.objects.get_or_create(user_id=int(user_id))
            sub.stripe_customer_id = customer_id
        else:
            return

    sub.tier = tier
    sub.status = "active"
    sub.stripe_subscription_id = subscription_id
    sub.save(update_fields=["tier", "status", "stripe_subscription_id",
                             "stripe_customer_id", "updated_at"])

    # Invalidate session cache for this user
    from django.contrib.sessions.models import Session as DjangoSession
    # Best effort — the middleware will re-read on next request anyway


def _handle_subscription_updated(subscription):
    """Sync subscription status changes."""
    sub_id = subscription.get("id", "")
    try:
        sub = UserSubscription.objects.get(stripe_subscription_id=sub_id)
    except UserSubscription.DoesNotExist:
        return

    status = subscription.get("status", "active")
    sub.status = status

    # Update period end
    period_end = subscription.get("current_period_end")
    if period_end:
        import datetime
        sub.current_period_end = datetime.datetime.fromtimestamp(
            period_end, tz=datetime.timezone.utc
        )

    # If canceled or unpaid, downgrade to FREE
    if status in ("canceled", "unpaid"):
        sub.tier = SubscriptionTier.FREE

    sub.save(update_fields=["status", "tier", "current_period_end", "updated_at"])


def _handle_subscription_deleted(subscription):
    """Downgrade user when subscription is fully canceled."""
    sub_id = subscription.get("id", "")
    try:
        sub = UserSubscription.objects.get(stripe_subscription_id=sub_id)
    except UserSubscription.DoesNotExist:
        return

    sub.tier = SubscriptionTier.FREE
    sub.status = "canceled"
    sub.save(update_fields=["tier", "status", "updated_at"])


def _handle_payment_failed(invoice):
    """Mark subscription as past_due on payment failure."""
    sub_id = invoice.get("subscription", "")
    if not sub_id:
        return
    try:
        sub = UserSubscription.objects.get(stripe_subscription_id=sub_id)
    except UserSubscription.DoesNotExist:
        return

    sub.status = "past_due"
    sub.save(update_fields=["status", "updated_at"])
```

- [ ] **Step 2: Create URLs**

`subscriptions/urls.py`:
```python
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
```

- [ ] **Step 3: Add to root URL config**

In `sports_edge/urls.py`, add the subscriptions include and a public landing page route. The full file should be:

```python
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
```

- [ ] **Step 4: Commit**

```bash
git add subscriptions/views.py subscriptions/urls.py sports_edge/urls.py
git commit -m "feat: add Stripe checkout, webhook, and portal views"
```

---

## Task 8: Subscription Templates

**Files:**
- Create: `subscriptions/templates/subscriptions/upgrade_required.html`
- Create: `subscriptions/templates/subscriptions/success.html`
- Create: `subscriptions/templates/subscriptions/cancel.html`
- Create: `templates/dashboard/partials/upgrade_prompt.html`

- [ ] **Step 1: Create upgrade_required.html**

`subscriptions/templates/subscriptions/upgrade_required.html`:
```html
{% extends "base.html" %}

{% block title %}Upgrade Required{% endblock %}

{% block content %}
<div class="flex items-center justify-center min-h-[60vh]">
  <div class="max-w-md w-full bg-slate-800 border border-slate-700 rounded-2xl p-8 text-center">
    <div class="text-5xl mb-4">🔒</div>
    <h1 class="text-2xl font-bold text-white mb-2">{{ required_tier }} Feature</h1>
    <p class="text-slate-400 mb-6">
      This feature requires a <span class="font-semibold text-white">{{ required_tier }}</span> subscription.
      {% if required_tier == "PRO" %}Unlock predictions, edge alerts, and Elo ratings for $19/mo.
      {% elif required_tier == "ELITE" %}Get everything including player props, backtests, and API access for $49/mo.
      {% endif %}
    </p>

    {% if error %}
    <p class="text-amber-400 text-sm mb-4">{{ error }}</p>
    {% endif %}

    <div class="space-y-3">
      <a href="{% url 'subscriptions:checkout' %}?tier={{ required_tier }}"
         class="block w-full py-3 px-4 bg-green-600 hover:bg-green-500 text-white font-semibold rounded-xl transition-colors">
        Upgrade to {{ required_tier }}
      </a>
      <a href="{% url 'dashboard:index' %}"
         class="block w-full py-3 px-4 bg-slate-700 hover:bg-slate-600 text-slate-300 font-medium rounded-xl transition-colors">
        Back to Dashboard
      </a>
    </div>
  </div>
</div>
{% endblock %}
```

- [ ] **Step 2: Create success.html**

`subscriptions/templates/subscriptions/success.html`:
```html
{% extends "base.html" %}

{% block title %}Welcome!{% endblock %}

{% block content %}
<div class="flex items-center justify-center min-h-[60vh]">
  <div class="max-w-md w-full bg-slate-800 border border-slate-700 rounded-2xl p-8 text-center">
    <div class="text-5xl mb-4">🎉</div>
    <h1 class="text-2xl font-bold text-white mb-2">Welcome to Sports Edge!</h1>
    <p class="text-slate-400 mb-6">
      Your subscription is now active. You have full access to all your tier's features.
    </p>
    <a href="{% url 'dashboard:index' %}"
       class="block w-full py-3 px-4 bg-green-600 hover:bg-green-500 text-white font-semibold rounded-xl transition-colors">
      Go to Dashboard
    </a>
  </div>
</div>
{% endblock %}
```

- [ ] **Step 3: Create cancel.html**

`subscriptions/templates/subscriptions/cancel.html`:
```html
{% extends "base.html" %}

{% block title %}Checkout Cancelled{% endblock %}

{% block content %}
<div class="flex items-center justify-center min-h-[60vh]">
  <div class="max-w-md w-full bg-slate-800 border border-slate-700 rounded-2xl p-8 text-center">
    <div class="text-5xl mb-4">👋</div>
    <h1 class="text-2xl font-bold text-white mb-2">Changed Your Mind?</h1>
    <p class="text-slate-400 mb-6">
      No worries! You can upgrade anytime. Your free account still has access to today's scores.
    </p>
    <div class="space-y-3">
      <a href="{% url 'landing' %}"
         class="block w-full py-3 px-4 bg-green-600 hover:bg-green-500 text-white font-semibold rounded-xl transition-colors">
        View Plans
      </a>
      <a href="{% url 'dashboard:index' %}"
         class="block w-full py-3 px-4 bg-slate-700 hover:bg-slate-600 text-slate-300 font-medium rounded-xl transition-colors">
        Back to Dashboard
      </a>
    </div>
  </div>
</div>
{% endblock %}
```

- [ ] **Step 4: Create inline upgrade_prompt partial**

`templates/dashboard/partials/upgrade_prompt.html`:
```html
{# Usage: {% include "dashboard/partials/upgrade_prompt.html" with tier="PRO" feature="predictions" %} #}
<div class="bg-slate-800/50 border border-dashed border-slate-600 rounded-xl p-6 text-center">
  <p class="text-slate-400 text-sm mb-3">
    🔒 Upgrade to <span class="font-semibold text-white">{{ tier }}</span> to unlock {{ feature }}
  </p>
  <a href="{% url 'subscriptions:checkout' %}?tier={{ tier }}"
     class="inline-block px-4 py-2 bg-green-600 hover:bg-green-500 text-white text-sm font-semibold rounded-lg transition-colors">
    Upgrade Now
  </a>
</div>
```

- [ ] **Step 5: Commit**

```bash
git add subscriptions/templates/ templates/dashboard/partials/upgrade_prompt.html
git commit -m "feat: add subscription templates — upgrade prompt, success, cancel pages"
```

---

## Task 9: Landing Page

**Files:**
- Create: `templates/landing.html`

- [ ] **Step 1: Create the landing page**

`templates/landing.html`:
```html
<!DOCTYPE html>
<html lang="en" class="h-full">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Sports Edge — Find Your Edge in Sports Betting</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script>
    tailwind.config = {
      theme: {
        extend: {
          colors: {
            'edge-green': '#22c55e',
            'edge-red': '#ef4444',
          }
        }
      }
    }
  </script>
</head>
<body class="bg-slate-900 text-slate-100 antialiased">

  <!-- Nav -->
  <nav class="border-b border-slate-800">
    <div class="max-w-6xl mx-auto px-4 py-4 flex items-center justify-between">
      <a href="/" class="flex items-center gap-2 text-xl font-bold text-white">
        <span class="text-2xl">⚡</span> Sports Edge
      </a>
      <div class="flex items-center gap-3">
        <a href="{% url 'accounts:login' %}" class="px-4 py-2 text-sm text-slate-300 hover:text-white transition-colors">Login</a>
        <a href="{% url 'accounts:register' %}" class="px-4 py-2 text-sm bg-green-600 hover:bg-green-500 text-white font-semibold rounded-lg transition-colors">Get Started Free</a>
      </div>
    </div>
  </nav>

  <!-- Hero -->
  <section class="max-w-6xl mx-auto px-4 pt-20 pb-16 text-center">
    <h1 class="text-5xl md:text-6xl font-extrabold tracking-tight">
      Find Your <span class="text-green-400">Edge</span> in Sports Betting
    </h1>
    <p class="mt-6 text-xl text-slate-400 max-w-2xl mx-auto">
      AI-powered predictions, real-time edge detection, and market analysis across NBA, NFL, NHL, MLB, and Soccer.
    </p>
    <div class="mt-8 flex items-center justify-center gap-4">
      <a href="{% url 'accounts:register' %}"
         class="px-8 py-3 bg-green-600 hover:bg-green-500 text-white font-bold rounded-xl text-lg transition-colors">
        Start Free
      </a>
      <a href="#pricing"
         class="px-8 py-3 bg-slate-800 hover:bg-slate-700 text-slate-300 font-semibold rounded-xl text-lg border border-slate-700 transition-colors">
        View Plans
      </a>
    </div>
  </section>

  <!-- Features -->
  <section class="max-w-6xl mx-auto px-4 py-16">
    <div class="grid md:grid-cols-3 gap-8">
      <div class="bg-slate-800 border border-slate-700 rounded-2xl p-6">
        <div class="text-3xl mb-3">🏀</div>
        <h3 class="text-lg font-bold text-white mb-2">5 Sports Covered</h3>
        <p class="text-slate-400 text-sm">NBA, NFL, NHL, MLB, and Soccer. Live scores, schedules, and injury reports updated throughout the day.</p>
      </div>
      <div class="bg-slate-800 border border-slate-700 rounded-2xl p-6">
        <div class="text-3xl mb-3">🤖</div>
        <h3 class="text-lg font-bold text-white mb-2">AI Predictions</h3>
        <p class="text-slate-400 text-sm">Ensemble models combining Elo ratings, logistic regression, and advanced stats. Backtested for accuracy.</p>
      </div>
      <div class="bg-slate-800 border border-slate-700 rounded-2xl p-6">
        <div class="text-3xl mb-3">📊</div>
        <h3 class="text-lg font-bold text-white mb-2">Edge Detection</h3>
        <p class="text-slate-400 text-sm">Automatically compares model predictions to market odds. Alerts you when there's value — with Kelly sizing.</p>
      </div>
    </div>
  </section>

  <!-- Pricing -->
  <section id="pricing" class="max-w-6xl mx-auto px-4 py-16">
    <h2 class="text-3xl font-bold text-center text-white mb-12">Simple, Transparent Pricing</h2>
    <div class="grid md:grid-cols-3 gap-8">

      <!-- Free -->
      <div class="bg-slate-800 border border-slate-700 rounded-2xl p-8">
        <h3 class="text-lg font-bold text-white">Free</h3>
        <div class="mt-4 mb-6">
          <span class="text-4xl font-extrabold text-white">$0</span>
          <span class="text-slate-400">/month</span>
        </div>
        <ul class="space-y-3 text-sm text-slate-300 mb-8">
          <li class="flex items-center gap-2"><span class="text-green-400">✓</span> Today's scores & schedules</li>
          <li class="flex items-center gap-2"><span class="text-green-400">✓</span> Basic standings</li>
          <li class="flex items-center gap-2"><span class="text-green-400">✓</span> 5 sports covered</li>
          <li class="flex items-center gap-2"><span class="text-slate-600">—</span> <span class="text-slate-500">Predictions locked</span></li>
          <li class="flex items-center gap-2"><span class="text-slate-600">—</span> <span class="text-slate-500">Edge alerts locked</span></li>
        </ul>
        <a href="{% url 'accounts:register' %}"
           class="block w-full py-3 text-center bg-slate-700 hover:bg-slate-600 text-white font-semibold rounded-xl transition-colors">
          Get Started
        </a>
      </div>

      <!-- Pro -->
      <div class="bg-slate-800 border-2 border-green-500 rounded-2xl p-8 relative">
        <span class="absolute -top-3 left-1/2 -translate-x-1/2 px-3 py-1 bg-green-600 text-white text-xs font-bold rounded-full">Most Popular</span>
        <h3 class="text-lg font-bold text-white">Pro</h3>
        <div class="mt-4 mb-6">
          <span class="text-4xl font-extrabold text-white">$19</span>
          <span class="text-slate-400">/month</span>
        </div>
        <ul class="space-y-3 text-sm text-slate-300 mb-8">
          <li class="flex items-center gap-2"><span class="text-green-400">✓</span> Everything in Free</li>
          <li class="flex items-center gap-2"><span class="text-green-400">✓</span> Win probability predictions</li>
          <li class="flex items-center gap-2"><span class="text-green-400">✓</span> Edge alerts & leaderboard</li>
          <li class="flex items-center gap-2"><span class="text-green-400">✓</span> Elo ratings & rankings</li>
          <li class="flex items-center gap-2"><span class="text-green-400">✓</span> Injury reports</li>
        </ul>
        <a href="{% url 'subscriptions:checkout' %}?tier=PRO"
           class="block w-full py-3 text-center bg-green-600 hover:bg-green-500 text-white font-bold rounded-xl transition-colors">
          Start Pro
        </a>
      </div>

      <!-- Elite -->
      <div class="bg-slate-800 border border-slate-700 rounded-2xl p-8">
        <h3 class="text-lg font-bold text-white">Elite</h3>
        <div class="mt-4 mb-6">
          <span class="text-4xl font-extrabold text-white">$49</span>
          <span class="text-slate-400">/month</span>
        </div>
        <ul class="space-y-3 text-sm text-slate-300 mb-8">
          <li class="flex items-center gap-2"><span class="text-green-400">✓</span> Everything in Pro</li>
          <li class="flex items-center gap-2"><span class="text-green-400">✓</span> Player prop projections</li>
          <li class="flex items-center gap-2"><span class="text-green-400">✓</span> Historical backtests</li>
          <li class="flex items-center gap-2"><span class="text-green-400">✓</span> Bet tracking & bankroll</li>
          <li class="flex items-center gap-2"><span class="text-green-400">✓</span> API access (coming soon)</li>
        </ul>
        <a href="{% url 'subscriptions:checkout' %}?tier=ELITE"
           class="block w-full py-3 text-center bg-purple-600 hover:bg-purple-500 text-white font-bold rounded-xl transition-colors">
          Start Elite
        </a>
      </div>
    </div>
  </section>

  <!-- Footer -->
  <footer class="border-t border-slate-800 mt-16">
    <div class="max-w-6xl mx-auto px-4 py-8 flex flex-col md:flex-row items-center justify-between gap-4 text-xs text-slate-500">
      <p>&copy; {% now "Y" %} Sports Edge Analytics</p>
      <p>NFL 🏈 &bull; NBA 🏀 &bull; NHL 🏒 &bull; MLB ⚾ &bull; Soccer ⚽</p>
      <p>For informational purposes only. Not financial advice.</p>
    </div>
  </footer>

</body>
</html>
```

- [ ] **Step 2: Commit**

```bash
git add templates/landing.html
git commit -m "feat: add public landing page with pricing table"
```

---

## Task 10: Gate Dashboard Views with Tier Checks

**Files:**
- Modify: `dashboard/views.py`

- [ ] **Step 1: Add tier gating to edge_leaderboard and backtest_results**

At the top of `dashboard/views.py`, add the import:

```python
from subscriptions.decorators import requires_tier
```

Add `@requires_tier("PRO")` before `@login_required` on `edge_leaderboard`:

```python
@requires_tier("PRO")
@login_required
def edge_leaderboard(request):
```

Add `@requires_tier("ELITE")` before `@login_required` on `backtest_results`:

```python
@requires_tier("ELITE")
@login_required
def backtest_results(request):
```

- [ ] **Step 2: Commit**

```bash
git add dashboard/views.py
git commit -m "feat: gate edge leaderboard (Pro) and backtests (Elite) with tier checks"
```

---

## Task 11: Gate Dashboard Templates with Tier Tags

**Files:**
- Modify: `templates/dashboard/index.html`
- Modify: `templates/dashboard/partials/games_list.html`

- [ ] **Step 1: Add tier gating to dashboard index**

At the top of `templates/dashboard/index.html`, add after `{% load humanize %}`:

```django
{% load subscription_tags %}
```

Wrap the "Top Edges" section (the right 1/3 column in the middle row) with tier check. Find the `<!-- Top Edges (1/3) -->` div and wrap it:

```django
{% if_tier "PRO" %}
<!-- Top Edges (1/3) -->
<div class="bg-slate-800 border border-slate-700 rounded-xl flex flex-col">
  ... existing top edges content ...
</div>
{% else_tier %}
<div class="bg-slate-800 border border-slate-700 rounded-xl flex flex-col">
  {% include "dashboard/partials/upgrade_prompt.html" with tier="PRO" feature="edge alerts and predictions" %}
</div>
{% endif_tier %}
```

- [ ] **Step 2: Add tier gating to games list partial**

At the top of `templates/dashboard/partials/games_list.html`, add after `{% load humanize %}`:

```django
{% load subscription_tags %}
```

Wrap the Away Win%, Home Win%, Total, and Edge columns in the table header and body with tier checks. Replace each prediction cell with:

```django
{% if_tier "PRO" %}
  <!-- existing prediction content -->
{% else_tier %}
  <span class="text-slate-600">🔒</span>
{% endif_tier %}
```

- [ ] **Step 3: Commit**

```bash
git add templates/dashboard/index.html templates/dashboard/partials/games_list.html
git commit -m "feat: add tier gating to dashboard index and games list templates"
```

---

## Task 12: Add Tier Badge to Nav & Subscription Link

**Files:**
- Modify: `templates/base.html`

- [ ] **Step 1: Add subscription tags and tier badge**

At the top of `templates/base.html`, after `<html lang="en" class="h-full">` and before `<head>`, we don't need to add template tags there. Instead, we need to load them inside the body. Since base.html doesn't have a `{% load %}` tag at the top, add it right after the opening `<body>` tag would not work — Django requires loads at the top. Add this at the very start of the file (line 1):

```django
{% load subscription_tags %}
```

In the user dropdown (after the "Signed in as" section, around line 150), add a tier badge:

```html
<div class="px-4 py-2 border-b border-slate-700">
  <p class="text-xs text-slate-400">Signed in as</p>
  <p class="text-sm font-semibold text-white truncate">{{ user.username }}</p>
  <span class="mt-1 inline-block px-2 py-0.5 rounded text-xs font-bold {% tier_badge_class %}">
    {% user_tier %}
  </span>
</div>
```

Add a "Manage Subscription" link in the dropdown, before the Logout separator:

```html
<a href="{% url 'subscriptions:portal' %}"
   class="flex items-center gap-2 px-4 py-2 text-sm text-slate-300 hover:text-white hover:bg-slate-700 transition-colors">
  <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 10h18M7 15h1m4 0h1m-7 4h12a3 3 0 003-3V8a3 3 0 00-3-3H6a3 3 0 00-3 3v8a3 3 0 003 3z" />
  </svg>
  Subscription
</a>
```

- [ ] **Step 2: Commit**

```bash
git add templates/base.html
git commit -m "feat: add tier badge and subscription link to nav dropdown"
```

---

## Task 13: Gate Game Detail & Sport Detail Templates

**Files:**
- Modify: `templates/dashboard/game_detail.html`
- Modify: `templates/dashboard/sport_detail.html`
- Modify: `templates/dashboard/edge_leaderboard.html`
- Modify: `templates/dashboard/backtest_results.html`

- [ ] **Step 1: Add tier tags to game_detail.html**

Add `{% load subscription_tags %}` at the top. Wrap prediction sections with `{% if_tier "PRO" %}`, player props with `{% if_tier "ELITE" %}`, showing upgrade prompts for locked content.

- [ ] **Step 2: Add tier tags to sport_detail.html**

Add `{% load subscription_tags %}` at the top. Wrap Elo leaderboard and edges with `{% if_tier "PRO" %}`, backtests with `{% if_tier "ELITE" %}`.

- [ ] **Step 3: Commit**

```bash
git add templates/dashboard/game_detail.html templates/dashboard/sport_detail.html
git commit -m "feat: add tier gating to game detail and sport detail templates"
```

---

## Task 14: Create UserSubscription for Existing Users (Data Migration)

**Files:**
- Create: `subscriptions/management/__init__.py`
- Create: `subscriptions/management/commands/__init__.py`
- Create: `subscriptions/management/commands/ensure_subscriptions.py`

- [ ] **Step 1: Create management command**

`subscriptions/management/__init__.py`:
```python
```

`subscriptions/management/commands/__init__.py`:
```python
```

`subscriptions/management/commands/ensure_subscriptions.py`:
```python
"""Ensure all existing users have a UserSubscription record."""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from subscriptions.models import UserSubscription

User = get_user_model()


class Command(BaseCommand):
    help = "Create UserSubscription records for existing users who don't have one."

    def handle(self, *args, **options):
        users_without = User.objects.exclude(subscription__isnull=False)
        created = 0
        for user in users_without:
            UserSubscription.objects.get_or_create(user=user)
            created += 1

        self.stdout.write(self.style.SUCCESS(f"Created {created} subscription records."))
```

- [ ] **Step 2: Commit**

```bash
git add subscriptions/management/
git commit -m "feat: add ensure_subscriptions command for existing users"
```

---

## Task 15: Update .gitignore and Final Cleanup

**Files:**
- Modify: `.gitignore` (add `.superpowers/`)

- [ ] **Step 1: Add .superpowers to .gitignore**

Append to `.gitignore`:
```
# Superpowers brainstorm files
.superpowers/
```

- [ ] **Step 2: Commit**

```bash
git add .gitignore
git commit -m "chore: add .superpowers to gitignore"
```
