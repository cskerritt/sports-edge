"""MMA/UFC data ingestor. Uses ESPN public API."""
import requests
from sports.models import (Game, GameStatus, League, Season, Sport, Team)
from .base import BaseIngestor

ESPN_MMA_BASE = "https://site.api.espn.com/apis/site/v2/sports/mma/ufc"

def _get_or_create_mma_league():
    league, _ = League.objects.get_or_create(
        sport=Sport.MMA, abbreviation="UFC",
        defaults={"name": "Ultimate Fighting Championship", "country": "USA"})
    return league

class MMAIngestor(BaseIngestor):
    sport = "MMA"

    def ingest_teams(self):
        # MMA doesn't have "teams" — fighters are represented as teams for compatibility
        result = self._empty_result()
        _get_or_create_mma_league()
        self._log_result("ingest_teams", result)
        return result

    def ingest_schedule(self, season_year):
        return self._empty_result()

    def ingest_scores(self, game_date=None):
        return self.ingest_espn_scoreboard(game_date=game_date)

    def ingest_injuries(self):
        return self._empty_result()
