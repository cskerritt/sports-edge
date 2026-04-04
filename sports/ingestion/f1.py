"""Formula 1 data ingestor. Uses ESPN public API."""
import requests
from sports.models import (Game, GameStatus, League, Season, Sport, Team)
from .base import BaseIngestor

ESPN_F1_BASE = "https://site.api.espn.com/apis/site/v2/sports/racing/f1"

def _get_or_create_f1_league():
    league, _ = League.objects.get_or_create(
        sport=Sport.F1, abbreviation="F1",
        defaults={"name": "Formula 1", "country": "International"})
    return league

class F1Ingestor(BaseIngestor):
    sport = "F1"

    def ingest_teams(self):
        result = self._empty_result()
        _get_or_create_f1_league()
        self._log_result("ingest_teams", result)
        return result

    def ingest_schedule(self, season_year):
        return self._empty_result()

    def ingest_scores(self, game_date=None):
        return self.ingest_espn_scoreboard(game_date=game_date)

    def ingest_injuries(self):
        return self._empty_result()
