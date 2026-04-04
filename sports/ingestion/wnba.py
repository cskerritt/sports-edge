"""WNBA data ingestor. Uses ESPN public API."""
import requests
from sports.models import (Game, GameStatus, InjuryReport, InjuryStatus, League, Player, Season, Sport, Team)
from .base import BaseIngestor

ESPN_WNBA_BASE = "https://site.api.espn.com/apis/site/v2/sports/basketball/wnba"

ESPN_INJURY_STATUS_MAP = {
    "Out": InjuryStatus.OUT, "Doubtful": InjuryStatus.DOUBTFUL,
    "Questionable": InjuryStatus.QUESTIONABLE, "Probable": InjuryStatus.PROBABLE,
    "Active": InjuryStatus.ACTIVE, "IR": InjuryStatus.IR, "Day-To-Day": InjuryStatus.QUESTIONABLE,
}

def _get_or_create_wnba_league():
    league, _ = League.objects.get_or_create(
        sport=Sport.WNBA, abbreviation="WNBA",
        defaults={"name": "Women's National Basketball Association", "country": "USA"})
    return league

class WNBAIngestor(BaseIngestor):
    sport = "WNBA"

    def ingest_teams(self):
        result = self._empty_result()
        league = _get_or_create_wnba_league()
        try:
            resp = requests.get(f"{ESPN_WNBA_BASE}/teams", params={"limit": 50}, timeout=15)
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
                    sport=Sport.WNBA, abbreviation=abbr,
                    defaults={"league": league, "name": name, "city": city, "espn_id": espn_id, "is_active": True})
                result["created" if created else "updated"] += 1
        except Exception as exc:
            self.logger.error("WNBA teams fetch failed: %s", exc)
            result["errors"] += 1
        self._log_result("ingest_teams", result)
        return result

    def ingest_schedule(self, season_year):
        return self._empty_result()

    def ingest_scores(self, game_date=None):
        return self.ingest_espn_scoreboard(game_date=game_date)

    def ingest_injuries(self):
        result = self._empty_result()
        today = self._today()
        try:
            resp = requests.get(f"{ESPN_WNBA_BASE}/injuries", timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            self.logger.error("WNBA injuries fetch failed: %s", exc)
            result["errors"] += 1
            return result
        for team_entry in self._extract_espn_injury_teams(data):
            team_data = team_entry.get("team", {})
            espn_team_id = str(team_data.get("id", ""))
            team = Team.objects.filter(sport=Sport.WNBA, espn_id=espn_team_id).first()
            for injury in team_entry.get("injuries", []):
                try:
                    athlete = injury.get("athlete", {})
                    athlete_id = str(athlete.get("id", ""))
                    athlete_name = athlete.get("displayName", "")
                    player = Player.objects.filter(sport=Sport.WNBA, espn_id=athlete_id).first()
                    if player is None and athlete_name:
                        player, _ = Player.objects.get_or_create(
                            sport=Sport.WNBA, espn_id=athlete_id,
                            defaults={"name": athlete_name, "first_name": athlete.get("firstName", ""),
                                      "last_name": athlete.get("lastName", ""),
                                      "position": athlete.get("position", {}).get("abbreviation", ""),
                                      "team": team, "is_active": True})
                    if player is None:
                        result["errors"] += 1
                        continue
                    raw_status = injury.get("status", "")
                    injury_status = ESPN_INJURY_STATUS_MAP.get(raw_status, InjuryStatus.QUESTIONABLE)
                    _, created = InjuryReport.objects.update_or_create(
                        player=player, report_date=today,
                        defaults={"status": injury_status,
                                  "body_part": injury.get("type", {}).get("description", ""),
                                  "description": injury.get("longComment", injury.get("shortComment", ""))})
                    result["created" if created else "updated"] += 1
                except Exception as exc:
                    self.logger.error("WNBA injury error: %s", exc)
                    result["errors"] += 1
        self._log_result("ingest_injuries", result)
        return result
