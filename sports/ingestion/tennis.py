"""Tennis data ingestor. Uses ESPN public API (ATP + WTA)."""
import requests
from sports.models import (Game, GameStatus, League, Season, Sport, Team)
from .base import BaseIngestor

ESPN_TENNIS_BASE = "https://site.api.espn.com/apis/site/v2/sports/tennis"

def _get_or_create_tennis_league():
    league, _ = League.objects.get_or_create(
        sport=Sport.TENNIS, abbreviation="ATP",
        defaults={"name": "ATP Tour", "country": "International"})
    return league

class TennisIngestor(BaseIngestor):
    sport = "TENNIS"

    def ingest_teams(self):
        result = self._empty_result()
        _get_or_create_tennis_league()
        # Tennis players are created from scoreboard events
        self._log_result("ingest_teams", result)
        return result

    def ingest_schedule(self, season_year):
        return self._empty_result()

    def ingest_scores(self, game_date=None):
        return self.ingest_espn_scoreboard(game_date=game_date)

    def ingest_injuries(self):
        return self._empty_result()
