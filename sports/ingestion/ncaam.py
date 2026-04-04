"""NCAA Men's Basketball data ingestor. Uses ESPN public API."""
import requests
from sports.models import (Game, GameStatus, InjuryReport, InjuryStatus, League, Player, Season, Sport, Team)
from .base import BaseIngestor

ESPN_NCAAM_BASE = "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball"

def _get_or_create_ncaam_league():
    league, _ = League.objects.get_or_create(
        sport=Sport.NCAAM, abbreviation="NCAAM",
        defaults={"name": "NCAA Men's Basketball", "country": "USA"})
    return league

class NCAAMIngestor(BaseIngestor):
    sport = "NCAAM"

    def ingest_teams(self):
        result = self._empty_result()
        league = _get_or_create_ncaam_league()
        # ESPN doesn't have a full teams list for college — teams are created
        # automatically when games are ingested via the scoreboard.
        # We fetch top 25 + conference groups to seed some teams.
        for group_id in [50, 55, 56, 46, 2, 1, 4, 8, 62]:  # Major conferences
            try:
                resp = requests.get(f"{ESPN_NCAAM_BASE}/teams", params={"groups": group_id, "limit": 50}, timeout=15)
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
                        sport=Sport.NCAAM, abbreviation=abbr,
                        defaults={"league": league, "name": name, "city": city, "espn_id": espn_id, "is_active": True})
                    result["created" if created else "updated"] += 1
            except Exception as exc:
                self.logger.warning("NCAAM teams group %s failed: %s", group_id, exc)
                result["errors"] += 1
        self._log_result("ingest_teams", result)
        return result

    def ingest_schedule(self, season_year):
        return self._empty_result()  # ESPN scoreboard handles game creation

    def ingest_scores(self, game_date=None):
        return self.ingest_espn_scoreboard(game_date=game_date)

    def ingest_injuries(self):
        return self._empty_result()  # No injury data for college
