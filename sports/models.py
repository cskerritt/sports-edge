from django.db import models
from django.utils import timezone


class Sport(models.TextChoices):
    NFL = "NFL", "NFL Football"
    NBA = "NBA", "NBA Basketball"
    NHL = "NHL", "NHL Hockey"
    MLB = "MLB", "MLB Baseball"
    SOCCER = "SOCCER", "Soccer"
    NCAAM = "NCAAM", "NCAA Men's Basketball"
    NCAAF = "NCAAF", "NCAA Football"
    MMA = "MMA", "MMA / UFC"
    WNBA = "WNBA", "WNBA Basketball"
    TENNIS = "TENNIS", "Tennis"
    GOLF = "GOLF", "PGA Golf"
    F1 = "F1", "Formula 1"


class GameStatus(models.TextChoices):
    SCHEDULED = "SCHEDULED", "Scheduled"
    IN_PROGRESS = "IN_PROGRESS", "In Progress"
    FINAL = "FINAL", "Final"
    POSTPONED = "POSTPONED", "Postponed"
    CANCELLED = "CANCELLED", "Cancelled"


class InjuryStatus(models.TextChoices):
    OUT = "OUT", "Out"
    DOUBTFUL = "DOUBTFUL", "Doubtful"
    QUESTIONABLE = "QUESTIONABLE", "Questionable"
    PROBABLE = "PROBABLE", "Probable"
    ACTIVE = "ACTIVE", "Active"
    IR = "IR", "Injured Reserve"


class League(models.Model):
    """Competition within a sport (e.g. Premier League, Champions League)."""
    sport = models.CharField(max_length=10, choices=Sport.choices)
    name = models.CharField(max_length=100)
    abbreviation = models.CharField(max_length=20)
    country = models.CharField(max_length=50, blank=True)
    external_id = models.CharField(max_length=50, blank=True)  # football-data.org id

    class Meta:
        unique_together = ("sport", "abbreviation")
        ordering = ["sport", "name"]

    def __str__(self):
        return f"{self.abbreviation} ({self.sport})"


class Season(models.Model):
    sport = models.CharField(max_length=10, choices=Sport.choices)
    league = models.ForeignKey(League, on_delete=models.CASCADE, null=True, blank=True, related_name="seasons")
    year = models.IntegerField()
    label = models.CharField(max_length=20)  # e.g. "2023-24", "2024"
    is_current = models.BooleanField(default=False)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)

    class Meta:
        unique_together = ("sport", "league", "year")
        ordering = ["-year"]

    def __str__(self):
        return f"{self.sport} {self.label}"


class Team(models.Model):
    sport = models.CharField(max_length=10, choices=Sport.choices)
    league = models.ForeignKey(League, on_delete=models.SET_NULL, null=True, blank=True, related_name="teams")
    name = models.CharField(max_length=100)
    abbreviation = models.CharField(max_length=10)
    city = models.CharField(max_length=100, blank=True)
    conference = models.CharField(max_length=50, blank=True)
    division = models.CharField(max_length=50, blank=True)
    venue = models.CharField(max_length=100, blank=True)
    venue_city = models.CharField(max_length=100, blank=True)
    # Timezone of home venue for travel/rest calculations
    venue_timezone = models.CharField(max_length=50, default="America/New_York")
    is_active = models.BooleanField(default=True)
    # External IDs for various data sources
    espn_id = models.CharField(max_length=20, blank=True)
    nfl_data_id = models.CharField(max_length=20, blank=True)
    nba_api_id = models.IntegerField(null=True, blank=True)
    nhl_api_id = models.IntegerField(null=True, blank=True)
    mlb_api_id = models.IntegerField(null=True, blank=True)
    football_data_id = models.IntegerField(null=True, blank=True)

    class Meta:
        unique_together = ("sport", "abbreviation")
        ordering = ["sport", "name"]
        indexes = [
            models.Index(fields=["sport"]),
            models.Index(fields=["sport", "is_active"]),
        ]

    def __str__(self):
        if self.city:
            return f"{self.city} {self.name}"
        return self.name

    @property
    def full_name(self):
        if self.city:
            return f"{self.city} {self.name}"
        return self.name


class Player(models.Model):
    team = models.ForeignKey(Team, on_delete=models.SET_NULL, null=True, blank=True, related_name="players")
    sport = models.CharField(max_length=10, choices=Sport.choices)
    name = models.CharField(max_length=100)
    first_name = models.CharField(max_length=50, blank=True)
    last_name = models.CharField(max_length=50, blank=True)
    position = models.CharField(max_length=20, blank=True)
    jersey_number = models.CharField(max_length=5, blank=True)
    is_active = models.BooleanField(default=True)
    # External IDs
    espn_id = models.CharField(max_length=20, blank=True)
    nfl_gsis_id = models.CharField(max_length=20, blank=True)
    nba_api_id = models.IntegerField(null=True, blank=True)
    nhl_api_id = models.IntegerField(null=True, blank=True)
    mlb_api_id = models.IntegerField(null=True, blank=True)

    class Meta:
        ordering = ["last_name", "first_name"]
        indexes = [
            models.Index(fields=["sport", "is_active"]),
            models.Index(fields=["team"]),
        ]

    def __str__(self):
        return f"{self.name} ({self.position}, {self.sport})"


class Game(models.Model):
    sport = models.CharField(max_length=10, choices=Sport.choices)
    season = models.ForeignKey(Season, on_delete=models.SET_NULL, null=True, blank=True, related_name="games")
    home_team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="home_games")
    away_team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="away_games")
    game_date = models.DateField()
    game_time = models.TimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=GameStatus.choices, default=GameStatus.SCHEDULED)
    # Scores
    home_score = models.IntegerField(null=True, blank=True)
    away_score = models.IntegerField(null=True, blank=True)
    # Period/quarter scores stored as JSON: {"1": 14, "2": 7, "3": 10, "4": 3, "OT": 0}
    home_period_scores = models.JSONField(default=dict, blank=True)
    away_period_scores = models.JSONField(default=dict, blank=True)
    overtime_periods = models.IntegerField(default=0)
    # Game context
    venue = models.CharField(max_length=100, blank=True)
    neutral_site = models.BooleanField(default=False)
    week = models.IntegerField(null=True, blank=True)  # NFL week number
    # Rest days (computed during ingestion)
    home_rest_days = models.IntegerField(null=True, blank=True)
    away_rest_days = models.IntegerField(null=True, blank=True)
    # External IDs
    external_id = models.CharField(max_length=50, blank=True)
    espn_id = models.CharField(max_length=20, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("sport", "external_id")
        ordering = ["-game_date", "game_time"]
        indexes = [
            models.Index(fields=["sport", "game_date"]),
            models.Index(fields=["sport", "status"]),
            models.Index(fields=["game_date"]),
        ]

    def __str__(self):
        return f"{self.away_team.abbreviation} @ {self.home_team.abbreviation} ({self.game_date})"

    @property
    def total_score(self):
        if self.home_score is not None and self.away_score is not None:
            return self.home_score + self.away_score
        return None

    @property
    def home_won(self):
        if self.status == GameStatus.FINAL and self.home_score is not None:
            return self.home_score > self.away_score
        return None

    @property
    def is_today(self):
        return self.game_date == timezone.localdate()

    @property
    def prediction(self):
        """Return the first ensemble prediction (set by prefetch_related to_attr='ensemble_predictions')."""
        preds = getattr(self, "ensemble_predictions", None)
        if preds:
            return preds[0]
        return None

    @property
    def best_edge(self):
        """Return the highest absolute edge from prefetched active_contracts."""
        contracts = getattr(self, "active_contracts", None)
        if not contracts:
            return None
        best = None
        for contract in contracts:
            for alert in getattr(contract, "open_alerts", []):
                edge_val = abs(alert.edge) if alert.edge else 0
                if best is None or edge_val > best:
                    best = edge_val
        return best


class InjuryReport(models.Model):
    player = models.ForeignKey(Player, on_delete=models.CASCADE, related_name="injuries")
    game = models.ForeignKey(Game, on_delete=models.SET_NULL, null=True, blank=True, related_name="injuries")
    report_date = models.DateField(default=timezone.now)
    status = models.CharField(max_length=20, choices=InjuryStatus.choices)
    body_part = models.CharField(max_length=50, blank=True)
    description = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-report_date", "player__last_name"]
        indexes = [models.Index(fields=["player", "report_date"])]

    def __str__(self):
        return f"{self.player.name} – {self.status} ({self.report_date})"


class TeamSeasonStats(models.Model):
    """Aggregated team stats per season for model features."""
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="season_stats")
    season = models.ForeignKey(Season, on_delete=models.CASCADE, related_name="team_stats")
    games_played = models.IntegerField(default=0)
    wins = models.IntegerField(default=0)
    losses = models.IntegerField(default=0)
    draws = models.IntegerField(default=0)  # soccer
    # Scoring
    points_per_game = models.FloatField(null=True, blank=True)
    points_allowed_per_game = models.FloatField(null=True, blank=True)
    # Pace (NBA: possessions per 48; NHL: shots per game; MLB: runs per game; Soccer: xG)
    pace = models.FloatField(null=True, blank=True)
    offensive_rating = models.FloatField(null=True, blank=True)
    defensive_rating = models.FloatField(null=True, blank=True)
    # Home/Away splits
    home_wins = models.IntegerField(default=0)
    home_losses = models.IntegerField(default=0)
    away_wins = models.IntegerField(default=0)
    away_losses = models.IntegerField(default=0)
    # Extra stats stored as JSON for sport-specific metrics
    extra_stats = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("team", "season")
        ordering = ["-season__year"]

    def __str__(self):
        return f"{self.team} – {self.season.label}"
