from django.contrib.auth.models import User
from django.db import models
from django.db.models import Sum, F
from sports.models import Sport
from markets.models import MarketContract


class BetOutcome(models.TextChoices):
    PENDING = "PENDING", "Pending"
    WON = "WON", "Won"
    LOST = "LOST", "Lost"
    PUSH = "PUSH", "Push"
    VOID = "VOID", "Void"


class BetRecord(models.Model):
    """Log of a placed prediction market bet."""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="bets")
    contract = models.ForeignKey(
        MarketContract, on_delete=models.SET_NULL, null=True, blank=True, related_name="bet_records"
    )
    sport = models.CharField(max_length=10, choices=Sport.choices)
    description = models.CharField(max_length=200)
    # YES or NO position
    is_yes = models.BooleanField(default=True)
    # Amount wagered in dollars
    amount_wagered = models.DecimalField(max_digits=10, decimal_places=2)
    # Entry price per share (0.00 – 1.00)
    entry_price = models.DecimalField(max_digits=6, decimal_places=4)
    # Shares purchased = amount_wagered / entry_price
    shares = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    # Model edge at time of bet
    predicted_edge = models.FloatField(null=True, blank=True)
    kelly_fraction_used = models.FloatField(null=True, blank=True)
    outcome = models.CharField(max_length=10, choices=BetOutcome.choices, default=BetOutcome.PENDING)
    # profit_loss: positive = win, negative = loss (filled when settled)
    profit_loss = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    placed_at = models.DateTimeField(auto_now_add=True)
    settled_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-placed_at"]
        indexes = [
            models.Index(fields=["user", "outcome"]),
            models.Index(fields=["user", "sport"]),
            models.Index(fields=["placed_at"]),
        ]

    def __str__(self):
        pos = "YES" if self.is_yes else "NO"
        return f"{self.description} ({pos} @ {self.entry_price}) – {self.outcome}"

    def save(self, *args, **kwargs):
        if self.amount_wagered and self.entry_price and self.entry_price > 0:
            self.shares = self.amount_wagered / self.entry_price
        super().save(*args, **kwargs)

    @property
    def max_payout(self):
        """Maximum payout if bet resolves correctly (shares × $1)."""
        if self.shares:
            return float(self.shares)
        return None

    @property
    def roi(self):
        if self.profit_loss and self.amount_wagered and self.amount_wagered > 0:
            return float(self.profit_loss) / float(self.amount_wagered)
        return None


class BankrollSnapshot(models.Model):
    """Daily P&L snapshot for charting."""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="bankroll_snapshots")
    date = models.DateField()
    starting_balance = models.DecimalField(max_digits=12, decimal_places=2)
    ending_balance = models.DecimalField(max_digits=12, decimal_places=2)
    total_wagered = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_profit_loss = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    active_bets = models.IntegerField(default=0)
    # Per-sport breakdown stored as JSON: {"NFL": 50.00, "NBA": -20.00, ...}
    sport_pnl = models.JSONField(default=dict, blank=True)

    class Meta:
        unique_together = ("user", "date")
        ordering = ["-date"]

    def __str__(self):
        return f"{self.user.username} – {self.date} – ${self.ending_balance}"

    @property
    def daily_pnl(self):
        return self.ending_balance - self.starting_balance


class UserBankrollSettings(models.Model):
    """Per-user bankroll configuration."""
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="bankroll_settings")
    initial_bankroll = models.DecimalField(max_digits=12, decimal_places=2, default=1000.00)
    current_balance = models.DecimalField(max_digits=12, decimal_places=2, default=1000.00)
    kelly_fraction = models.FloatField(default=0.25, help_text="Fraction of Kelly to use (0.25 = quarter-Kelly)")
    max_bet_pct = models.FloatField(default=0.05, help_text="Max % of bankroll per bet")
    edge_threshold = models.FloatField(default=0.05, help_text="Minimum edge to flag as alert")

    def __str__(self):
        return f"{self.user.username} bankroll settings"

    @classmethod
    def get_for_user(cls, user):
        obj, _ = cls.objects.get_or_create(user=user)
        return obj
