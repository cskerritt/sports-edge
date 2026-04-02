from django.db import models
from sports.models import Team, Game, Player, Season, Sport


class EloRating(models.Model):
    """Track Elo rating for a team over time."""
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="elo_ratings")
    season = models.ForeignKey(Season, on_delete=models.SET_NULL, null=True, blank=True, related_name="elo_ratings")
    date = models.DateField()
    rating = models.FloatField(default=1500.0)
    games_played = models.IntegerField(default=0)
    # After-game snapshot: set when this rating was established by a game result
    game = models.ForeignKey(Game, on_delete=models.SET_NULL, null=True, blank=True, related_name="elo_snapshots")

    class Meta:
        ordering = ["-date"]
        unique_together = ("team", "game")
        indexes = [
            models.Index(fields=["team", "date"]),
            models.Index(fields=["team", "season"]),
        ]

    def __str__(self):
        return f"{self.team} – {self.rating:.1f} ({self.date})"


class GamePrediction(models.Model):
    """Model output for a scheduled or completed game."""
    MODEL_VERSION_CHOICES = [
        ("elo_v1", "Elo v1"),
        ("logistic_v1", "Logistic Regression v1"),
        ("ensemble_v1", "Ensemble v1"),
    ]

    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name="predictions")
    model_version = models.CharField(max_length=20, choices=MODEL_VERSION_CHOICES, default="ensemble_v1")
    # Win probabilities (should sum to 1.0)
    home_win_prob = models.FloatField()
    away_win_prob = models.FloatField()
    draw_prob = models.FloatField(default=0.0)  # soccer only
    # Spread / total predictions
    predicted_spread = models.FloatField(null=True, blank=True)  # positive = home favored
    predicted_total = models.FloatField(null=True, blank=True)
    # Component probabilities (home team wins used as primary signal)
    elo_home_win_prob = models.FloatField(null=True, blank=True)
    logistic_home_win_prob = models.FloatField(null=True, blank=True)
    # Confidence: 0-1, higher is better
    confidence = models.FloatField(default=0.5)
    # Adjustments applied (stored for audit)
    adjustments_applied = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("game", "model_version")
        ordering = ["-game__game_date"]
        indexes = [models.Index(fields=["game", "model_version"])]

    def __str__(self):
        return f"{self.game} – Home {self.home_win_prob:.1%} ({self.model_version})"

    @property
    def implied_home_odds(self):
        """Convert home win probability to American odds."""
        p = self.home_win_prob
        if p <= 0 or p >= 1:
            return None
        if p >= 0.5:
            return round(-(p / (1 - p)) * 100)
        return round(((1 - p) / p) * 100)

    @property
    def implied_away_odds(self):
        p = self.away_win_prob
        if p <= 0 or p >= 1:
            return None
        if p >= 0.5:
            return round(-(p / (1 - p)) * 100)
        return round(((1 - p) / p) * 100)


class PlayerPropProjection(models.Model):
    """Projected player stats for prop bets."""
    PROP_TYPES = [
        # NBA
        ("NBA_PTS", "NBA Points"),
        ("NBA_REB", "NBA Rebounds"),
        ("NBA_AST", "NBA Assists"),
        ("NBA_3PM", "NBA 3-Pointers Made"),
        ("NBA_STL", "NBA Steals"),
        ("NBA_BLK", "NBA Blocks"),
        ("NBA_PRA", "NBA Pts+Reb+Ast"),
        # NFL
        ("NFL_PASS_YDS", "NFL Passing Yards"),
        ("NFL_PASS_TDS", "NFL Passing TDs"),
        ("NFL_RUSH_YDS", "NFL Rushing Yards"),
        ("NFL_REC_YDS", "NFL Receiving Yards"),
        ("NFL_RECS", "NFL Receptions"),
        ("NFL_RUSH_ATT", "NFL Rush Attempts"),
        # MLB
        ("MLB_HITS", "MLB Hits"),
        ("MLB_STRIKEOUTS", "MLB Pitcher Strikeouts"),
        ("MLB_HR", "MLB Home Runs"),
        ("MLB_RBI", "MLB RBIs"),
        # NHL
        ("NHL_GOALS", "NHL Goals"),
        ("NHL_ASSISTS", "NHL Assists"),
        ("NHL_SHOTS", "NHL Shots on Goal"),
        ("NHL_SAVES", "NHL Goalie Saves"),
        # Soccer
        ("SOC_SHOTS", "Soccer Shots"),
        ("SOC_GOALS", "Soccer Goals"),
    ]

    player = models.ForeignKey(Player, on_delete=models.CASCADE, related_name="prop_projections")
    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name="prop_projections")
    prop_type = models.CharField(max_length=20, choices=PROP_TYPES)
    projected_value = models.FloatField()
    floor_value = models.FloatField(null=True, blank=True)  # 10th percentile
    ceiling_value = models.FloatField(null=True, blank=True)  # 90th percentile
    confidence = models.FloatField(default=0.5)
    games_sampled = models.IntegerField(default=0)  # rolling window used
    model_version = models.CharField(max_length=20, default="rolling_v1")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("player", "game", "prop_type", "model_version")
        ordering = ["-game__game_date"]
        indexes = [models.Index(fields=["player", "game"])]

    def __str__(self):
        return f"{self.player.name} – {self.get_prop_type_display()} {self.projected_value:.1f}"


class BacktestResult(models.Model):
    """Track historical model accuracy for a sport/model version."""
    sport = models.CharField(max_length=10, choices=Sport.choices)
    model_version = models.CharField(max_length=20)
    season = models.ForeignKey(Season, on_delete=models.SET_NULL, null=True, blank=True)
    # Moneyline accuracy
    total_games = models.IntegerField(default=0)
    correct_predictions = models.IntegerField(default=0)
    accuracy = models.FloatField(null=True, blank=True)
    # Calibration (how well probabilities match actual outcomes)
    brier_score = models.FloatField(null=True, blank=True)
    log_loss = models.FloatField(null=True, blank=True)
    # Against the market
    roi = models.FloatField(null=True, blank=True)
    avg_edge = models.FloatField(null=True, blank=True)
    # Over/under accuracy
    total_total_games = models.IntegerField(default=0)
    correct_totals = models.IntegerField(default=0)
    totals_accuracy = models.FloatField(null=True, blank=True)
    computed_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("sport", "model_version", "season")
        ordering = ["-computed_at"]

    def __str__(self):
        acc = f"{self.accuracy:.1%}" if self.accuracy else "N/A"
        return f"{self.sport} {self.model_version} – {acc} accuracy"
