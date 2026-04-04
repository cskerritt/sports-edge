# Sports Edge — Full Product Improvement Design

## Overview

Four-workstream improvement to Sports Edge: fix NBA game display, add Stripe tiered subscriptions, gate dashboard features by tier, and polish the UI with a public landing page.

## Approach

Fix-First, Then Layer — each workstream builds on a working foundation. NBA fix ships first, Stripe layered in cleanly, dashboard polished last when all data and gating logic is in place.

---

## Workstream 1: Fix NBA Game Display

### Problem

NBA games may not appear on the dashboard despite the recent ESPN scoreboard fix (cedc1b6). Remaining issues:

- Timezone mismatch: `timezone.now().date()` in views may not match game dates stored from ESPN
- ESPN ID backfill may not have run, causing team lookup failures in scoreboard ingestion
- Empty DB after fresh deploy has no games at all

### Solution

1. **Verify timezone handling** in `dashboard/views.py` — ensure `today` uses the project timezone (America/New_York), not UTC
2. **Ensure ESPN ID backfill** runs as part of `ingest_teams()` (already implemented, verify it works)
3. **Add a `refresh_today` management command** that force-runs `ingest_scores()` for today's date across all sports — useful for debugging and manual recovery
4. **Add fallback date logic** — if no games found for today, check if games exist for yesterday/tomorrow (timezone edge case detection)

### Files Changed

- `dashboard/views.py` — timezone-aware date handling
- `sports/management/commands/refresh_today.py` — new command
- `sports/ingestion/base.py` — verify ESPN scoreboard date handling

---

## Workstream 2: Stripe Subscription System

### New App: `subscriptions/`

#### Models

**SubscriptionTier** (TextChoices enum):
- `FREE` — $0/month
- `PRO` — $19/month
- `ELITE` — $49/month

**UserSubscription** (OneToOneField → User):
- `tier` — SubscriptionTier, default FREE
- `stripe_customer_id` — CharField, blank/null
- `stripe_subscription_id` — CharField, blank/null
- `status` — CharField: active, canceled, past_due, trialing
- `current_period_end` — DateTimeField, null
- `created_at`, `updated_at` — auto timestamps

Auto-created for new users via post_save signal on User.

#### Stripe Integration

**Package:** `stripe` added to requirements.txt

**Settings (env vars):**
- `STRIPE_SECRET_KEY`
- `STRIPE_PUBLISHABLE_KEY`
- `STRIPE_WEBHOOK_SECRET`
- `STRIPE_PRO_PRICE_ID`
- `STRIPE_ELITE_PRICE_ID`

**Endpoints:**
- `POST /subscriptions/checkout/` — Creates Stripe Checkout Session, redirects to Stripe
- `POST /subscriptions/webhook/` — Receives Stripe webhook events (CSRF-exempt)
- `GET /subscriptions/portal/` — Creates Stripe Customer Portal session, redirects
- `GET /subscriptions/success/` — Post-checkout success page
- `GET /subscriptions/cancel/` — Post-checkout cancel page

**Webhook Events Handled:**
- `checkout.session.completed` — Create/update UserSubscription with tier, stripe IDs
- `customer.subscription.updated` — Sync status and period_end
- `customer.subscription.deleted` — Set tier back to FREE, status to canceled
- `invoice.payment_failed` — Set status to past_due

**Webhook Security:** Verify signature using `STRIPE_WEBHOOK_SECRET` via `stripe.Webhook.construct_event()`.

#### Access Control

**Middleware** (`subscriptions/middleware.py`):
- Runs on every request for authenticated users
- Attaches `request.subscription_tier` (string: FREE/PRO/ELITE)
- Caches tier in session to avoid DB hit on every request
- Cache invalidated by webhook updates

**Decorator** (`subscriptions/decorators.py`):
```python
@requires_tier("PRO")
def edge_leaderboard(request):
    ...
```
- Returns upgrade prompt page if user's tier is insufficient
- No 403 — shows a friendly upgrade card

**Template Tag** (`subscriptions/templatetags/subscription_tags.py`):
```django
{% load subscription_tags %}
{% if_tier "PRO" %}
  <!-- pro content -->
{% else %}
  <!-- upgrade prompt -->
{% endif_tier %}
```

---

## Workstream 3: Tier-Gated Features

### Gating Map

| View / Feature | Free | Pro | Elite |
|---|---|---|---|
| Dashboard index (scores) | Yes | Yes | Yes |
| Game cards (teams, time, score) | Yes | Yes | Yes |
| Win probability predictions | No | Yes | Yes |
| Edge alerts & badges | No | Yes | Yes |
| Edge leaderboard page | No | Yes | Yes |
| Injury reports | No | Yes | Yes |
| Elo ratings & leaderboard | No | Yes | Yes |
| Sport detail page (full) | No | Yes | Yes |
| Player props & projections | No | No | Yes |
| Historical backtests | No | No | Yes |
| Bet tracking & bankroll | No | No | Yes |
| Game detail (full) | No | Partial | Yes |
| API access | No | No | Yes |

### Upgrade Prompts

When a free user accesses gated content:
- View-level gates (e.g., edge leaderboard): render `subscriptions/upgrade_required.html` with tier name, price, and Stripe Checkout CTA
- Template-level gates (e.g., prediction bar in game card): show blurred/faded placeholder with inline "Upgrade to Pro" link

### Implementation

- Add `@requires_tier` to: `edge_leaderboard`, `backtest_results`
- Add template conditionals to: `index.html`, `today_games.html`, `game_detail.html`, `sport_detail.html`
- Add `game_detail` partial gating: free sees basic info, pro sees predictions + markets, elite sees props

---

## Workstream 4: Dashboard Polish & Landing Page

### Landing Page (`/` for unauthenticated users)

- Public, no login required
- Hero: "Find Your Edge in Sports Betting" + signup CTA
- Live stats: today's game count, model accuracy from latest backtests
- Feature grid: icons + descriptions for each major feature
- Pricing table: 3-column (Free/Pro/Elite) with feature checkmarks and Stripe Checkout buttons
- Footer: links to login, about, terms

### Dashboard Redesign

**Theme:** Dark background (#0f1117), card-based layout, sport-specific accent colors:
- NBA: #F58426 (orange)
- NFL: #013369 (navy) / #D50A0A (red)
- NHL: #000000 / #A2AAAD
- MLB: #002D72 (blue) / #E31937 (red)
- Soccer: #6B21A8 (purple)

**Layout:**
- Sidebar nav: Dashboard, Today's Games, Edge Alerts, Sports (with sub-items), Backtests, My Bets, Subscription
- Top bar: sport filter pills (with emoji), search, user menu with tier badge (FREE/PRO/ELITE colored)
- Main content: card grid, responsive

**Game Cards:**
- Team abbreviations with sport-colored badges
- Score or game time
- Horizontal win probability bar (gradient from away color to home color)
- Edge badge (green if positive, red if negative) with magnitude
- Click to expand or navigate to game detail

**Edge Leaderboard:**
- Clean table with alternating row shading
- Edge value color-coded by strength (light yellow < 5%, green 5-10%, bright green > 10%)
- Sport badge, matchup, model prob vs market prob, edge %, Kelly fraction

**Game Detail:**
- Two-column: left = matchup + predictions, right = market contracts + edges
- Injury table below
- Player props table (elite only)

### New Pages

- `/pricing/` — Public pricing page (mirrors landing page pricing section)
- `/subscriptions/manage/` — Authenticated, links to Stripe Customer Portal
- `/subscriptions/success/` — "Welcome to Pro/Elite!" confirmation
- `/subscriptions/cancel/` — "Changed your mind?" with re-subscribe CTA

### Tech Stack (unchanged)

- Django templates + HTMX for dynamic filtering
- No SPA/React — keeps stack simple
- CSS custom properties for theming
- No CSS framework — custom styles for full control

---

## File Structure (new/modified)

```
subscriptions/                    # NEW APP
  __init__.py
  admin.py
  apps.py
  models.py                      # UserSubscription, SubscriptionTier
  views.py                       # checkout, webhook, portal, success, cancel
  urls.py
  middleware.py                   # SubscriptionTierMiddleware
  decorators.py                  # requires_tier
  signals.py                     # auto-create UserSubscription on User create
  templatetags/
    subscription_tags.py         # if_tier template tag
  templates/
    subscriptions/
      upgrade_required.html
      success.html
      cancel.html

dashboard/views.py               # MODIFIED — timezone fix, tier gating
templates/
  landing.html                   # NEW — public landing page
  dashboard/
    base.html                    # MODIFIED — new nav, dark theme
    index.html                   # MODIFIED — tier-gated sections
    today_games.html             # MODIFIED — new game cards
    game_detail.html             # MODIFIED — two-column, tier-gated
    edge_leaderboard.html        # MODIFIED — new table design
    sport_detail.html            # MODIFIED — tier-gated sections
    backtest_results.html        # MODIFIED — tier-gated
    pricing.html                 # NEW
  partials/
    game_card.html               # NEW — reusable game card component
    upgrade_prompt.html          # NEW — inline upgrade CTA

sports/management/commands/
  refresh_today.py               # NEW

sports_edge/settings/base.py     # MODIFIED — Stripe settings, subscriptions app
sports_edge/urls.py              # MODIFIED — subscriptions URLs, landing page
requirements.txt                 # MODIFIED — add stripe package
```

---

## Out of Scope

- Pay-per-pick / credits system (future iteration)
- API access implementation (Elite tier feature, endpoints TBD)
- Email notifications for edge alerts
- Mobile app
- Real-time WebSocket updates
