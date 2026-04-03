"""
Email sending via Resend API.

Usage:
    from sports_edge.email import send_email
    send_email(
        to="user@example.com",
        subject="New Edge Alert: Lakers vs Celtics",
        html="<h1>Edge detected!</h1><p>+7.2% edge on Lakers ML</p>",
    )
"""

import logging

from django.conf import settings

logger = logging.getLogger("email")


def send_email(to, subject, html, from_email=None):
    """Send an email via Resend. Returns the Resend response or None on failure."""
    api_key = getattr(settings, "RESEND_API_KEY", "")
    if not api_key:
        logger.warning("RESEND_API_KEY not set, skipping email to %s", to)
        return None

    import resend
    resend.api_key = api_key

    sender = from_email or getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@sports-edge.com")

    if isinstance(to, str):
        to = [to]

    try:
        response = resend.Emails.send({
            "from": sender,
            "to": to,
            "subject": subject,
            "html": html,
        })
        logger.info("Email sent to %s: %s", to, subject)
        return response
    except Exception as exc:
        logger.error("Failed to send email to %s: %s", to, exc)
        return None


def send_edge_alert_email(user, edge_alert):
    """Send an edge alert notification to a user."""
    if not user.email:
        return None

    contract = edge_alert.contract
    game = contract.game if contract else None
    matchup = f"{game.away_team} @ {game.home_team}" if game else contract.title

    html = f"""
    <div style="font-family: -apple-system, sans-serif; max-width: 500px; margin: 0 auto;">
      <div style="background: #0f172a; color: white; padding: 24px; border-radius: 12px;">
        <h2 style="margin: 0 0 8px 0; color: #22c55e;">Edge Alert</h2>
        <p style="margin: 0 0 16px 0; color: #94a3b8; font-size: 14px;">{edge_alert.sport}</p>

        <div style="background: #1e293b; padding: 16px; border-radius: 8px; margin-bottom: 16px;">
          <p style="margin: 0 0 4px 0; font-size: 18px; font-weight: bold;">{matchup}</p>
          <p style="margin: 0; color: #94a3b8; font-size: 14px;">{contract.title}</p>
        </div>

        <div style="display: flex; gap: 16px; margin-bottom: 16px;">
          <div>
            <p style="margin: 0; color: #94a3b8; font-size: 12px;">Edge</p>
            <p style="margin: 0; font-size: 24px; font-weight: bold; color: #22c55e;">
              +{edge_alert.edge_pct:.1f}%
            </p>
          </div>
          <div>
            <p style="margin: 0; color: #94a3b8; font-size: 12px;">Kelly</p>
            <p style="margin: 0; font-size: 24px; font-weight: bold;">
              {edge_alert.kelly_pct:.1f}%
            </p>
          </div>
        </div>

        <p style="margin: 0; color: #64748b; font-size: 12px;">
          Sports Edge Analytics — For informational purposes only.
        </p>
      </div>
    </div>
    """

    return send_email(
        to=user.email,
        subject=f"Edge Alert: {matchup} (+{edge_alert.edge_pct:.1f}%)",
        html=html,
    )


def send_subscription_welcome_email(user, tier):
    """Send welcome email after subscription upgrade."""
    if not user.email:
        return None

    tier_features = {
        "PRO": "predictions, edge alerts, Elo ratings, and injury reports",
        "ELITE": "everything in Pro plus player props, backtests, bet tracking, and API access",
    }
    features = tier_features.get(tier, "premium features")

    html = f"""
    <div style="font-family: -apple-system, sans-serif; max-width: 500px; margin: 0 auto;">
      <div style="background: #0f172a; color: white; padding: 24px; border-radius: 12px;">
        <h2 style="margin: 0 0 8px 0;">Welcome to {tier}!</h2>
        <p style="margin: 0 0 16px 0; color: #94a3b8;">
          Hey {user.username}, your subscription is now active.
        </p>
        <p style="margin: 0 0 16px 0;">
          You now have access to {features}.
        </p>
        <a href="#" style="display: inline-block; background: #22c55e; color: white;
           padding: 12px 24px; border-radius: 8px; text-decoration: none; font-weight: bold;">
          Go to Dashboard
        </a>
        <p style="margin: 16px 0 0 0; color: #64748b; font-size: 12px;">
          Sports Edge Analytics
        </p>
      </div>
    </div>
    """

    return send_email(
        to=user.email,
        subject=f"Welcome to Sports Edge {tier}!",
        html=html,
    )
