"""NASCAR data ingestor. Uses ESPN public API."""
import requests
from sports.models import (Game, GameStatus, League, Season, Sport, Team)
from .base import BaseIngestor

ESPN_NASCAR_BASE = "https://site.api.espn.com/apis/site/v2/sports/racing/nascar"

def _get_or_create_nascar_league():
    league, _ = League.objects.get_or_create(
        sport=Sport.NASCAR, abbreviation="NASCAR",
        defaults={"name": "NASCAR Cup Series", "country": "USA"})
    return league

class NASCARIngestor(BaseIngestor):
    sport = "NASCAR"

    def ingest_teams(self):
        result = self._empty_result()
        _get_or_create_nascar_league()
        self._log_result("ingest_teams", result)
        return result

    def ingest_schedule(self, season_year):
        return self._empty_result()

    def ingest_scores(self, game_date=None):
        return self.ingest_espn_scoreboard(game_date=game_date)

    def ingest_injuries(self):
        return self._empty_result()
