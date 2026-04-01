from django.db import models
from sports.models import Game, Sport


class ContractType(models.TextChoices):
    HOME_WIN = "HOME_WIN", "Home Win"
    AWAY_WIN = "AWAY_WIN", "Away Win"
    DRAW = "DRAW", "Draw"
    OVER = "OVER", "Over"
    UNDER = "UNDER", "Under"
    PLAYER_PROP = "PLAYER_PROP", "Player Prop"
    OTHER = "OTHER", "Other"


class MarketSource(models.TextChoices):
    COINBASE = "COINBASE", "Coinbase"
    KALSHI = "KALSHI", "Kalshi"


class MarketContract(models.Model):
    """A prediction market contract (Coinbase or Kalshi)."""
    game = models.ForeignKey(Game, on_delete=models.SET_NULL, null=True, blank=True, related_name="contracts")
    sport = models.CharField(max_length=10, choices=Sport.choices)
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    contract_type = models.CharField(max_length=20, choices=ContractType.choices, default=ContractType.OTHER)
    # Data source
    source = models.CharField(max_length=20, choices=MarketSource.choices, default=MarketSource.COINBASE)
    # External identifiers — coinbase_product_id doubles as a unique external key for all sources
    coinbase_product_id = models.CharField(max_length=100, unique=True)
    coinbase_contract_id = models.CharField(max_length=100, blank=True)
    # Over/under line (if applicable)
    line = models.FloatField(null=True, blank=True)
    # Timing
    game_date = models.DateField(null=True, blank=True)
    expiry = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    is_resolved = models.BooleanField(default=False)
    resolution = models.BooleanField(null=True, blank=True)  # True=YES resolved, False=NO resolved
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-game_date", "sport"]
        indexes = [
            models.Index(fields=["sport", "is_active"]),
            models.Index(fields=["game_date"]),
            models.Index(fields=["is_active", "is_resolved"]),
        ]

    def __str__(self):
        return f"{self.title} ({self.sport})"


class MarketPrice(models.Model):
    """Price snapshot for a market contract."""
    contract = models.ForeignKey(MarketContract, on_delete=models.CASCADE, related_name="prices")
    yes_price = models.FloatField()   # 0.0 – 1.0 (probability implied by market)
    no_price = models.FloatField()    # 0.0 – 1.0
    mid_price = models.FloatField()   # (yes + (1-no)) / 2 for spread-adjusted
    volume_24h = models.FloatField(null=True, blank=True)
    open_interest = models.FloatField(null=True, blank=True)
    fetched_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-fetched_at"]
        indexes = [models.Index(fields=["contract", "fetched_at"])]
        get_latest_by = "fetched_at"

    def __str__(self):
        return f"{self.contract.title} – YES {self.yes_price:.3f} ({self.fetched_at:%Y-%m-%d %H:%M})"


class EdgeAlert(models.Model):
    """A flagged value bet where model disagrees with market by ≥ threshold."""
    STATUS_CHOICES = [
        ("OPEN", "Open"),
        ("EXPIRED", "Expired"),
        ("HIT", "Resolved – Model Correct"),
        ("MISS", "Resolved – Model Wrong"),
    ]

    contract = models.ForeignKey(MarketContract, on_delete=models.CASCADE, related_name="edge_alerts")
    # The market price snapshot this alert was generated from
    market_price = models.ForeignKey(MarketPrice, on_delete=models.SET_NULL, null=True, related_name="alerts")
    sport = models.CharField(max_length=10, choices=Sport.choices)
    # Model's probability for the YES outcome
    model_probability = models.FloatField()
    # Market's implied probability for the YES outcome
    market_probability = models.FloatField()
    # Edge = model_prob - market_prob (positive = model says higher than market)
    edge = models.FloatField()
    # Kelly fraction recommended
    kelly_fraction = models.FloatField()
    # Confidence in the model estimate
    confidence = models.FloatField(default=0.5)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="OPEN")
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-edge", "-created_at"]
        indexes = [
            models.Index(fields=["sport", "status"]),
            models.Index(fields=["status", "created_at"]),
        ]

    def __str__(self):
        direction = "YES" if self.edge > 0 else "NO"
        return f"{self.contract.title} – {direction} edge {abs(self.edge):.1%}"

    @property
    def abs_edge(self):
        return abs(self.edge)

    @property
    def edge_pct(self):
        return self.edge * 100

    @property
    def kelly_pct(self):
        return self.kelly_fraction * 100
