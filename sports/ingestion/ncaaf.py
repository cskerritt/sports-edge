"""NCAA Football data ingestor. Uses ESPN public API."""
import requests
from sports.models import (Game, GameStatus, InjuryReport, InjuryStatus, League, Player, Season, Sport, Team)
from .base import BaseIngestor

ESPN_NCAAF_BASE = "https://site.api.espn.com/apis/site/v2/sports/football/college-football"

def _get_or_create_ncaaf_league():
    league, _ = League.objects.get_or_create(
        sport=Sport.NCAAF, abbreviation="NCAAF",
        defaults={"name": "NCAA Football", "country": "USA"})
    return league

class NCAAFIngestor(BaseIngestor):
    sport = "NCAAF"

    def ingest_teams(self):
        result = self._empty_result()
        league = _get_or_create_ncaaf_league()
        for group_id in [80, 81, 1, 4, 5, 8, 9, 12, 15, 17, 18, 37]:  # Major conferences
            try:
                resp = requests.get(f"{ESPN_NCAAF_BASE}/teams", params={"groups": group_id, "limit": 50}, timeout=15)
                resp.raise_for_status()
                data = resp.json()
                teams = data.get("sports", [{}])[0].get("leagues", [{}])[0].get("teams", [])
                if not teams:
                    teams = data.get("teams", [])
                for entry in teams:
                    td = entry.get("team", entry)
                    espn_id = str(td.get("id", ""))
                    abbr = td.get("abbreviation", "").upper()
                    name = td.get("shortDisplayName", td.get("displayName", ""))
                    city = td.get("location", "")
                    if not abbr:
                        continue
                    _, created = Team.objects.update_or_create(
                        sport=Sport.NCAAF, abbreviation=abbr,
                        defaults={"league": league, "name": name, "city": city, "espn_id": espn_id, "is_active": True})
                    result["created" if created else "updated"] += 1
            except Exception as exc:
                self.logger.warning("NCAAF teams group %s failed: %s", group_id, exc)
                result["errors"] += 1
        self._log_result("ingest_teams", result)
        return result

    def ingest_schedule(self, season_year):
        return self._empty_result()

    def ingest_scores(self, game_date=None):
        return self.ingest_espn_scoreboard(game_date=game_date)

    def ingest_injuries(self):
        return self._empty_result()
