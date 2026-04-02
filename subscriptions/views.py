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


def _handle_subscription_updated(subscription):
    """Sync subscription status changes."""
    sub_id = subscription.get("id", "")
    try:
        sub = UserSubscription.objects.get(stripe_subscription_id=sub_id)
    except UserSubscription.DoesNotExist:
        return

    status = subscription.get("status", "active")
    sub.status = status

    period_end = subscription.get("current_period_end")
    if period_end:
        import datetime
        sub.current_period_end = datetime.datetime.fromtimestamp(
            period_end, tz=datetime.timezone.utc
        )

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
