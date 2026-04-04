"""Cricket data ingestor. Uses ESPN public API."""
import requests
from sports.models import (Game, GameStatus, League, Season, Sport, Team)
from .base import BaseIngestor

ESPN_CRICKET_BASE = "https://site.api.espn.com/apis/site/v2/sports/cricket"

def _get_or_create_cricket_league():
    league, _ = League.objects.get_or_create(
        sport=Sport.CRICKET, abbreviation="ICC",
        defaults={"name": "International Cricket Council", "country": "International"})
    return league

class CricketIngestor(BaseIngestor):
    sport = "CRICKET"

    def ingest_teams(self):
        result = self._empty_result()
        _get_or_create_cricket_league()
        self._log_result("ingest_teams", result)
        return result

    def ingest_schedule(self, season_year):
        return self._empty_result()

    def ingest_scores(self, game_date=None):
        return self.ingest_espn_scoreboard(game_date=game_date)

    def ingest_injuries(self):
        return self._empty_result()
