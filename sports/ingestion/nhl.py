"""
NHL data ingestor.

Data sources:
- NHL Web API (api-web.nhle.com/v1) — teams, schedule, live scores
- ESPN public API                    — injuries
"""

import datetime

import requests

from sports.models import (
    Game,
    GameStatus,
    InjuryReport,
    InjuryStatus,
    League,
    Player,
    Season,
    Sport,
    Team,
    TeamSeasonStats,
)

from .base import BaseIngestor

ESPN_NHL_BASE = "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl"
NHL_STATS_REST = "https://api.nhle.com/stats/rest/en"

ESPN_INJURY_STATUS_MAP = {
    "Out": InjuryStatus.OUT,
    "Doubtful": InjuryStatus.DOUBTFUL,
    "Questionable": InjuryStatus.QUESTIONABLE,
    "Probable": InjuryStatus.PROBABLE,
    "Active": InjuryStatus.ACTIVE,
    "Injured Reserve": InjuryStatus.IR,
    "IR": InjuryStatus.IR,
    "Day-To-Day": InjuryStatus.QUESTIONABLE,
}

# NHL API game state codes
NHL_GAME_STATE_MAP = {
    "FUT": GameStatus.SCHEDULED,
    "PRE": GameStatus.SCHEDULED,
    "LIVE": GameStatus.IN_PROGRESS,
    "CRIT": GameStatus.IN_PROGRESS,
    "OVER": GameStatus.FINAL,
    "FINAL": GameStatus.FINAL,
    "OFF": GameStatus.FINAL,
}


def _get_or_create_nhl_league() -> League:
    league, _ = League.objects.get_or_create(
        sport=Sport.NHL,
        abbreviation="NHL",
        defaults={"name": "National Hockey League", "country": "USA/Canada"},
    )
    return league


def _nhl_season_id(season_year: int) -> str:
    """Convert 2024 → '20242025' (NHL API season format)."""
    return f"{season_year}{season_year + 1}"


class NHLIngestor(BaseIngestor):
    sport = "NHL"
    BASE = "https://api-web.nhle.com/v1"

    # ------------------------------------------------------------------
    # Teams
    # ------------------------------------------------------------------

    def ingest_teams(self) -> dict:
        """
        Fetch NHL teams from the stats REST API and upsert Team objects.
        Falls back to standings endpoint if REST fails.
        """
        result = self._empty_result()
        league = _get_or_create_nhl_league()

        # Primary: stats REST API
        url = f"{NHL_STATS_REST}/team"
        try:
            resp = requests.get(url, timeout=15)
            resp.raise_for_status()
            raw = resp.json()
            teams_list = raw.get("data", [])
        except Exception as exc:
            self.logger.warning("NHL stats REST team endpoint failed: %s — trying standings fallback", exc)
            teams_list = []

        if not teams_list:
            # Fallback: extract from current standings
            try:
                resp = requests.get(f"{self.BASE}/standings/now", timeout=15)
                resp.raise_for_status()
                standings = resp.json()
                teams_list = [
                    {
                        "triCode": s.get("teamAbbrev", {}).get("default", ""),
                        "fullName": s.get("teamName", {}).get("default", ""),
                        "id": s.get("teamId"),
                    }
                    for s in standings.get("standings", [])
                ]
            except Exception as exc:
                self.logger.error("NHL standings fallback also failed: %s", exc)
                result["errors"] += 1
                return result

        for t in teams_list:
            try:
                tri_code = (t.get("triCode") or t.get("abbrev") or "").upper()
                if not tri_code:
                    continue

                full_name = t.get("fullName") or t.get("name", {}).get("default", tri_code)
                team_id = t.get("id")

                # Split city from nickname heuristically (last word is nickname)
                parts = full_name.rsplit(" ", 1)
                nickname = parts[-1] if len(parts) > 1 else full_name
                city = parts[0] if len(parts) > 1 else ""

                obj, created = Team.objects.update_or_create(
                    sport=Sport.NHL,
                    abbreviation=tri_code,
                    defaults={
                        "league": league,
                        "name": nickname,
                        "city": city,
                        "nhl_api_id": team_id,
                        "is_active": True,
                    },
                )
                if created:
                    result["created"] += 1
                else:
                    result["updated"] += 1
            except Exception as exc:
                self.logger.error("Error upserting NHL team %s: %s", t.get("triCode"), exc)
                result["errors"] += 1

        self._log_result("ingest_teams", result)
        return result

    # ------------------------------------------------------------------
    # Schedule
    # ------------------------------------------------------------------

    def ingest_schedule(self, season_year: int) -> dict:
        """
        Iteratively fetch the NHL schedule week-by-week for *season_year*.

        Strategy: start from Oct 1 of season_year, call GET /schedule/{date}
        which returns a week of games, advance by 7 days until season end
        (approx June 30 of season_year+1).
        """
        result = self._empty_result()
        league = _get_or_create_nhl_league()
        season_id = _nhl_season_id(season_year)

        season_label = f"{season_year}-{str(season_year + 1)[-2:]}"
        season_obj, _ = Season.objects.get_or_create(
            sport=Sport.NHL,
            league=league,
            year=season_year,
            defaults={"label": season_label, "is_current": False},
        )

        current_date = datetime.date(season_year, 10, 1)
        season_end = datetime.date(season_year + 1, 7, 1)
        visited_game_ids: set = set()

        while current_date <= season_end:
            date_str = current_date.strftime("%Y-%m-%d")
            url = f"{self.BASE}/schedule/{date_str}"
            try:
                resp = requests.get(url, timeout=15)
                resp.raise_for_status()
                data = resp.json()
            except Exception as exc:
                self.logger.warning("NHL schedule fetch failed for %s: %s", date_str, exc)
                result["errors"] += 1
                current_date += datetime.timedelta(days=7)
                continue

            for game_week in data.get("gameWeek", []):
                for game in game_week.get("games", []):
                    try:
                        game_id = str(game.get("id", ""))
                        if not game_id or game_id in visited_game_ids:
                            continue
                        visited_game_ids.add(game_id)

                        # Only include games from this season
                        game_season = str(game.get("season", ""))
                        if game_season and game_season != season_id:
                            continue

                        away_team_data = game.get("awayTeam", {})
                        home_team_data = game.get("homeTeam", {})
                        away_abbr = away_team_data.get("abbrev", "").upper()
                        home_abbr = home_team_data.get("abbrev", "").upper()

                        try:
                            home_team = Team.objects.get(sport=Sport.NHL, abbreviation=home_abbr)
                            away_team = Team.objects.get(sport=Sport.NHL, abbreviation=away_abbr)
                        except Team.DoesNotExist:
                            self.logger.warning(
                                "NHL team not found: home=%s away=%s", home_abbr, away_abbr
                            )
                            result["errors"] += 1
                            continue

                        # Game date from startTimeUTC
                        start_utc = game.get("startTimeUTC", "")
                        try:
                            if start_utc:
                                dt = datetime.datetime.fromisoformat(start_utc.replace("Z", "+00:00"))
                                game_date = dt.date()
                                game_time = dt.time()
                            else:
                                game_date = current_date
                                game_time = None
                        except (ValueError, TypeError):
                            game_date = current_date
                            game_time = None

                        # State
                        game_state = game.get("gameState", "FUT").upper()
                        status = NHL_GAME_STATE_MAP.get(game_state, GameStatus.SCHEDULED)

                        # Scores
                        home_score_raw = home_team_data.get("score")
                        away_score_raw = away_team_data.get("score")
                        try:
                            home_score = int(home_score_raw) if home_score_raw is not None else None
                            away_score = int(away_score_raw) if away_score_raw is not None else None
                        except (ValueError, TypeError):
                            home_score = away_score = None

                        venue_name = game.get("venue", {}).get("default", "")

                        obj, created = Game.objects.update_or_create(
                            sport=Sport.NHL,
                            external_id=game_id,
                            defaults={
                                "season": season_obj,
                                "home_team": home_team,
                                "away_team": away_team,
                                "game_date": game_date,
                                "game_time": game_time,
                                "status": status,
                                "home_score": home_score,
                                "away_score": away_score,
                                "venue": venue_name,
                            },
                        )
                        if created:
                            result["created"] += 1
                        else:
                            result["updated"] += 1

                    except Exception as exc:
                        self.logger.error("Error processing NHL game %s: %s", game.get("id"), exc)
                        result["errors"] += 1

            current_date += datetime.timedelta(days=7)

        self._log_result("ingest_schedule", result)
        return result

    # ------------------------------------------------------------------
    # Scores
    # ------------------------------------------------------------------

    def ingest_scores(self, game_date=None) -> dict:
        """Fetch completed/live scores for *game_date* using GET /score/{date}."""
        result = self._empty_result()

        target_date = game_date or self._today()
        if isinstance(target_date, datetime.datetime):
            target_date = target_date.date()

        date_str = target_date.strftime("%Y-%m-%d")
        url = f"{self.BASE}/score/{date_str}"

        try:
            resp = requests.get(url, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            self.logger.error("NHL score fetch failed for %s: %s", date_str, exc)
            result["errors"] += 1
            return result

        for game in data.get("games", []):
            try:
                game_id = str(game.get("id", ""))
                if not game_id:
                    continue

                game_state = game.get("gameState", "FUT").upper()
                status = NHL_GAME_STATE_MAP.get(game_state, GameStatus.SCHEDULED)

                home_team_data = game.get("homeTeam", {})
                away_team_data = game.get("awayTeam", {})
                home_score_raw = home_team_data.get("score")
                away_score_raw = away_team_data.get("score")
                try:
                    home_score = int(home_score_raw) if home_score_raw is not None else None
                    away_score = int(away_score_raw) if away_score_raw is not None else None
                except (ValueError, TypeError):
                    home_score = away_score = None

                game_obj = Game.objects.filter(sport=Sport.NHL, external_id=game_id).first()
                if game_obj is None:
                    result["errors"] += 1
                    continue

                update_fields = {"status": status}
                if home_score is not None:
                    update_fields["home_score"] = home_score
                if away_score is not None:
                    update_fields["away_score"] = away_score

                for k, v in update_fields.items():
                    setattr(game_obj, k, v)
                game_obj.save(update_fields=list(update_fields.keys()) + ["updated_at"])
                result["updated"] += 1

            except Exception as exc:
                self.logger.error("Error processing NHL score for game %s: %s", game.get("id"), exc)
                result["errors"] += 1

        self._log_result("ingest_scores", result)
        return result

    # ------------------------------------------------------------------
    # Injuries
    # ------------------------------------------------------------------

    def ingest_injuries(self) -> dict:
        """Fetch NHL injuries from the ESPN API."""
        result = self._empty_result()
        today = self._today()

        url = f"{ESPN_NHL_BASE}/injuries"
        try:
            resp = requests.get(url, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            self.logger.error("Failed to fetch ESPN NHL injuries: %s", exc)
            result["errors"] += 1
            return result

        for team_entry in data:
            team_data = team_entry.get("team", {})
            espn_team_id = str(team_data.get("id", ""))
            team = Team.objects.filter(sport=Sport.NHL, espn_id=espn_team_id).first()

            for injury in team_entry.get("injuries", []):
                try:
                    athlete = injury.get("athlete", {})
                    athlete_id = str(athlete.get("id", ""))
                    athlete_name = athlete.get("displayName", "")

                    player = Player.objects.filter(sport=Sport.NHL, espn_id=athlete_id).first()
                    if player is None and athlete_name:
                        player, _ = Player.objects.get_or_create(
                            sport=Sport.NHL,
                            espn_id=athlete_id,
                            defaults={
                                "name": athlete_name,
                                "first_name": athlete.get("firstName", ""),
                                "last_name": athlete.get("lastName", ""),
                                "position": athlete.get("position", {}).get("abbreviation", ""),
                                "team": team,
                                "is_active": True,
                            },
                        )

                    if player is None:
                        result["errors"] += 1
                        continue

                    raw_status = injury.get("status", "")
                    injury_status = ESPN_INJURY_STATUS_MAP.get(raw_status, InjuryStatus.QUESTIONABLE)
                    body_part = injury.get("type", {}).get("description", "")
                    description = injury.get("longComment", injury.get("shortComment", ""))

                    _, created = InjuryReport.objects.update_or_create(
                        player=player,
                        report_date=today,
                        defaults={
                            "status": injury_status,
                            "body_part": body_part,
                            "description": description,
                        },
                    )
                    if created:
                        result["created"] += 1
                    else:
                        result["updated"] += 1

                except Exception as exc:
                    self.logger.error(
                        "Error processing NHL injury for %s: %s",
                        injury.get("athlete", {}).get("displayName", "unknown"),
                        exc,
                    )
                    result["errors"] += 1

        self._log_result("ingest_injuries", result)
        return result

    # ------------------------------------------------------------------
    # Team stats
    # ------------------------------------------------------------------

    def ingest_team_stats(self, season_year: int) -> dict:
        """
        Fetch NHL team stats from the stats REST API for *season_year*.
        Stores goals-per-game, goals-against, shots, power-play % etc. in TeamSeasonStats.
        """
        result = self._empty_result()
        league = _get_or_create_nhl_league()
        season_id = _nhl_season_id(season_year)
        season_label = f"{season_year}-{str(season_year + 1)[-2:]}"
        season_obj, _ = Season.objects.get_or_create(
            sport=Sport.NHL,
            league=league,
            year=season_year,
            defaults={"label": season_label, "is_current": False},
        )

        url = f"{NHL_STATS_REST}/team/summary"
        params = {
            "cayenneExp": f"seasonId={season_id} and gameTypeId=2",  # gameTypeId=2 → regular season
            "limit": -1,
        }
        try:
            resp = requests.get(url, params=params, timeout=20)
            resp.raise_for_status()
            data = resp.json()
            stats_list = data.get("data", [])
        except Exception as exc:
            self.logger.error("NHL team summary stats failed: %s", exc)
            result["errors"] += 1
            return result

        for row in stats_list:
            try:
                tri_code = str(row.get("teamFullName", "")).upper()
                # Prefer lookup by triCode if present
                abbrev = str(row.get("teamAbbrev") or tri_code).upper()
                team = Team.objects.filter(sport=Sport.NHL, abbreviation=abbrev).first()
                if team is None:
                    result["errors"] += 1
                    continue

                gp = int(row.get("gamesPlayed", 0) or 0)
                wins = int(row.get("wins", 0) or 0)
                losses = int(row.get("losses", 0) or 0)

                goals_for = row.get("goalsFor")
                goals_against = row.get("goalsAgainst")
                gpg = float(goals_for) / gp if goals_for and gp else None
                gapg = float(goals_against) / gp if goals_against and gp else None

                shots_pg = None
                shots_for = row.get("shotsForPerGame")
                if shots_for:
                    try:
                        shots_pg = float(shots_for)
                    except (ValueError, TypeError):
                        pass

                extra_stats = {}
                skip_keys = {"teamFullName", "teamAbbrev"}
                for k, v in row.items():
                    if k in skip_keys:
                        continue
                    if isinstance(v, (int, float)):
                        try:
                            extra_stats[k] = round(float(v), 4)
                        except (ValueError, TypeError):
                            pass
                    elif isinstance(v, str):
                        extra_stats[k] = v

                obj, created = TeamSeasonStats.objects.update_or_create(
                    team=team,
                    season=season_obj,
                    defaults={
                        "games_played": gp,
                        "wins": wins,
                        "losses": losses,
                        "points_per_game": gpg,
                        "points_allowed_per_game": gapg,
                        "pace": shots_pg,
                        "extra_stats": extra_stats,
                    },
                )
                if created:
                    result["created"] += 1
                else:
                    result["updated"] += 1

            except Exception as exc:
                self.logger.error("Error saving NHL team stats for %s: %s", row.get("teamAbbrev"), exc)
                result["errors"] += 1

        self._log_result("ingest_team_stats", result)
        return result

    # ------------------------------------------------------------------
    # Orchestration override
    # ------------------------------------------------------------------

    def run_full_ingest(self, season_year: int) -> dict:
        results = super().run_full_ingest(season_year)
        try:
            stats_result = self.ingest_team_stats(season_year)
        except Exception as exc:
            self.logger.exception("Unhandled error in ingest_team_stats: %s", exc)
            stats_result = {"created": 0, "updated": 0, "errors": 1}
        self._log_result("ingest_team_stats", stats_result)
        results["ingest_team_stats"] = stats_result
        return results
