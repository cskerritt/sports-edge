"""
MLB data ingestor.

Data sources:
- MLB Stats API (statsapi.mlb.com) — teams, schedule, live scores
- pybaseball                        — team batting / pitching stats
- ESPN public API                   — injuries
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

ESPN_MLB_BASE = "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb"

ESPN_INJURY_STATUS_MAP = {
    "Out": InjuryStatus.OUT,
    "Doubtful": InjuryStatus.DOUBTFUL,
    "Questionable": InjuryStatus.QUESTIONABLE,
    "Probable": InjuryStatus.PROBABLE,
    "Active": InjuryStatus.ACTIVE,
    "Injured Reserve": InjuryStatus.IR,
    "10-Day IL": InjuryStatus.IR,
    "60-Day IL": InjuryStatus.IR,
    "IR": InjuryStatus.IR,
    "Day-To-Day": InjuryStatus.QUESTIONABLE,
}

# MLB Stats API abstract game state codes
MLB_ABSTRACT_STATE_MAP = {
    "Preview": GameStatus.SCHEDULED,
    "Live": GameStatus.IN_PROGRESS,
    "Final": GameStatus.FINAL,
}


def _get_or_create_mlb_league() -> League:
    league, _ = League.objects.get_or_create(
        sport=Sport.MLB,
        abbreviation="MLB",
        defaults={"name": "Major League Baseball", "country": "USA"},
    )
    return league


class MLBIngestor(BaseIngestor):
    sport = "MLB"
    STATSAPI_BASE = "https://statsapi.mlb.com/api/v1"

    # ------------------------------------------------------------------
    # Teams
    # ------------------------------------------------------------------

    def ingest_teams(self) -> dict:
        """Fetch all MLB teams from the Stats API and upsert Team objects."""
        result = self._empty_result()
        league = _get_or_create_mlb_league()

        url = f"{self.STATSAPI_BASE}/teams"
        try:
            resp = requests.get(url, params={"sportId": 1}, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            self.logger.error("Failed to fetch MLB teams: %s", exc)
            result["errors"] += 1
            return result

        for t in data.get("teams", []):
            try:
                team_id = t.get("id")
                abbreviation = t.get("abbreviation", "").upper()
                club_name = t.get("clubName", t.get("teamName", ""))  # e.g. "Cubs"
                location = t.get("locationName", "")                  # e.g. "Chicago"
                venue_name = t.get("venue", {}).get("name", "")
                division = t.get("division", {}).get("name", "")
                conference = t.get("league", {}).get("name", "")     # AL / NL

                if not abbreviation:
                    continue

                obj, created = Team.objects.update_or_create(
                    sport=Sport.MLB,
                    abbreviation=abbreviation,
                    defaults={
                        "league": league,
                        "name": club_name,
                        "city": location,
                        "venue": venue_name,
                        "division": division,
                        "conference": conference,
                        "mlb_api_id": team_id,
                        "is_active": True,
                    },
                )
                if created:
                    result["created"] += 1
                else:
                    result["updated"] += 1
            except Exception as exc:
                self.logger.error("Error upserting MLB team %s: %s", t.get("abbreviation"), exc)
                result["errors"] += 1

        # Backfill ESPN IDs
        self._backfill_espn_ids()

        self._log_result("ingest_teams", result)
        return result

    def _backfill_espn_ids(self):
        """Fetch ESPN team data and set espn_id on existing Team records."""
        url = f"{ESPN_MLB_BASE}/teams"
        try:
            resp = requests.get(url, params={"limit": 100}, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            self.logger.warning("ESPN MLB teams fetch failed (espn_id backfill): %s", exc)
            return

        try:
            raw_teams = data["sports"][0]["leagues"][0]["teams"]
        except (KeyError, IndexError):
            raw_teams = data.get("teams", [])

        for entry in raw_teams:
            team_data = entry.get("team", entry)
            espn_id = str(team_data.get("id", ""))
            abbreviation = team_data.get("abbreviation", "").upper()
            if not espn_id or not abbreviation:
                continue
            Team.objects.filter(
                sport=Sport.MLB, abbreviation=abbreviation, espn_id=""
            ).update(espn_id=espn_id)

    # ------------------------------------------------------------------
    # Schedule
    # ------------------------------------------------------------------

    def ingest_schedule(self, season_year: int) -> dict:
        """Fetch the regular-season schedule for *season_year* from the Stats API."""
        result = self._empty_result()
        league = _get_or_create_mlb_league()

        season_obj, _ = Season.objects.get_or_create(
            sport=Sport.MLB,
            league=league,
            year=season_year,
            defaults={"label": str(season_year), "is_current": False},
        )

        url = f"{self.STATSAPI_BASE}/schedule"
        params = {
            "sportId": 1,
            "season": season_year,
            "gameType": "R",          # Regular season
            "hydrate": "team,venue",
            "limit": 2500,
        }
        try:
            resp = requests.get(url, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            self.logger.error("Failed to fetch MLB schedule for %s: %s", season_year, exc)
            result["errors"] += 1
            return result

        for date_entry in data.get("dates", []):
            for game in date_entry.get("games", []):
                try:
                    game_pk = str(game.get("gamePk", ""))
                    if not game_pk:
                        continue

                    teams = game.get("teams", {})
                    home_data = teams.get("home", {})
                    away_data = teams.get("away", {})

                    home_abbr = home_data.get("team", {}).get("abbreviation", "").upper()
                    away_abbr = away_data.get("team", {}).get("abbreviation", "").upper()

                    try:
                        home_team = Team.objects.get(sport=Sport.MLB, abbreviation=home_abbr)
                        away_team = Team.objects.get(sport=Sport.MLB, abbreviation=away_abbr)
                    except Team.DoesNotExist:
                        self.logger.warning(
                            "MLB team not found: home=%s away=%s — run ingest_teams first",
                            home_abbr,
                            away_abbr,
                        )
                        result["errors"] += 1
                        continue

                    game_date_str = game.get("officialDate") or game.get("gameDate", "")[:10]
                    try:
                        game_date = datetime.date.fromisoformat(game_date_str)
                    except (ValueError, TypeError):
                        result["errors"] += 1
                        continue

                    game_time = None
                    game_date_raw = game.get("gameDate", "")
                    if "T" in game_date_raw:
                        try:
                            dt = datetime.datetime.fromisoformat(game_date_raw.replace("Z", "+00:00"))
                            game_time = dt.time()
                        except (ValueError, TypeError):
                            pass

                    abstract_state = game.get("status", {}).get("abstractGameState", "Preview")
                    status = MLB_ABSTRACT_STATE_MAP.get(abstract_state, GameStatus.SCHEDULED)

                    # Scores
                    home_score_raw = home_data.get("score")
                    away_score_raw = away_data.get("score")
                    try:
                        home_score = int(home_score_raw) if home_score_raw is not None else None
                        away_score = int(away_score_raw) if away_score_raw is not None else None
                    except (ValueError, TypeError):
                        home_score = away_score = None

                    venue_name = game.get("venue", {}).get("name", "")

                    obj, created = Game.objects.update_or_create(
                        sport=Sport.MLB,
                        external_id=game_pk,
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
                    self.logger.error("Error processing MLB game %s: %s", game.get("gamePk"), exc)
                    result["errors"] += 1

        self._log_result("ingest_schedule", result)
        return result

    # ------------------------------------------------------------------
    # Scores
    # ------------------------------------------------------------------

    def ingest_scores(self, game_date=None) -> dict:
        """Fetch today's MLB games from ESPN scoreboard.

        Uses the shared ESPN scoreboard method which creates games that
        don't exist yet and updates scores for games that do.
        """
        return self.ingest_espn_scoreboard(game_date=game_date)

    # ------------------------------------------------------------------
    # Injuries
    # ------------------------------------------------------------------

    def ingest_injuries(self) -> dict:
        """Fetch MLB injuries from the ESPN API."""
        result = self._empty_result()
        today = self._today()

        url = f"{ESPN_MLB_BASE}/injuries"
        try:
            resp = requests.get(url, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            self.logger.error("Failed to fetch ESPN MLB injuries: %s", exc)
            result["errors"] += 1
            return result

        for team_entry in self._extract_espn_injury_teams(data):
            team_data = team_entry.get("team", {})
            espn_team_id = str(team_data.get("id", ""))
            team = Team.objects.filter(sport=Sport.MLB, espn_id=espn_team_id).first()

            for injury in team_entry.get("injuries", []):
                try:
                    athlete = injury.get("athlete", {})
                    athlete_id = str(athlete.get("id", ""))
                    athlete_name = athlete.get("displayName", "")

                    player = Player.objects.filter(sport=Sport.MLB, espn_id=athlete_id).first()
                    if player is None and athlete_name:
                        player, _ = Player.objects.get_or_create(
                            sport=Sport.MLB,
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
                        "Error processing MLB injury for %s: %s",
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
        Import team batting and pitching stats via pybaseball for *season_year*
        and persist them in TeamSeasonStats.
        """
        result = self._empty_result()

        try:
            import pybaseball
        except ImportError:
            self.logger.error("pybaseball is not installed. Run: pip install pybaseball")
            result["errors"] += 1
            return result

        league = _get_or_create_mlb_league()
        season_obj, _ = Season.objects.get_or_create(
            sport=Sport.MLB,
            league=league,
            year=season_year,
            defaults={"label": str(season_year), "is_current": False},
        )

        # --- Batting ---
        batting_df = None
        try:
            pybaseball.cache.enable()
            batting_df = pybaseball.team_batting(season_year)
        except Exception as exc:
            self.logger.error("pybaseball.team_batting(%s) failed: %s", season_year, exc)
            result["errors"] += 1

        # --- Pitching ---
        pitching_df = None
        try:
            pitching_df = pybaseball.team_pitching(season_year)
        except Exception as exc:
            self.logger.error("pybaseball.team_pitching(%s) failed: %s", season_year, exc)
            result["errors"] += 1

        if batting_df is None and pitching_df is None:
            return result

        # Collect all team abbreviations present across both DataFrames
        team_abbrevs: set = set()
        if batting_df is not None and not batting_df.empty:
            team_abbrevs.update(str(a).upper() for a in batting_df.get("Team", batting_df.index).tolist())
        if pitching_df is not None and not pitching_df.empty:
            team_abbrevs.update(str(a).upper() for a in pitching_df.get("Team", pitching_df.index).tolist())

        for abbr in team_abbrevs:
            try:
                team = Team.objects.filter(sport=Sport.MLB, abbreviation=abbr).first()
                if team is None:
                    self.logger.warning("MLB team not found for abbrev: %s", abbr)
                    result["errors"] += 1
                    continue

                extra_stats: dict = {}
                gp = 0
                runs_per_game = None
                era = None
                ops_val = None

                if batting_df is not None and not batting_df.empty:
                    bat_row = None
                    if "Team" in batting_df.columns:
                        matches = batting_df[batting_df["Team"].str.upper() == abbr]
                        if not matches.empty:
                            bat_row = matches.iloc[0]
                    else:
                        # Index may be team name
                        try:
                            bat_row = batting_df.loc[abbr]
                        except KeyError:
                            pass

                    if bat_row is not None:
                        gp = int(bat_row.get("G", 0) or 0)
                        r = bat_row.get("R")
                        if r is not None and gp:
                            try:
                                runs_per_game = round(float(r) / gp, 4)
                            except (ValueError, TypeError, ZeroDivisionError):
                                pass
                        ops_raw = bat_row.get("OPS")
                        if ops_raw is not None:
                            try:
                                ops_val = round(float(ops_raw), 4)
                            except (ValueError, TypeError):
                                pass

                        for col in batting_df.columns:
                            val = bat_row.get(col)
                            if isinstance(val, (int, float)):
                                try:
                                    fval = float(val)
                                    if fval != fval:  # NaN check
                                        continue
                                    extra_stats[f"bat_{col}"] = round(fval, 4)
                                except (ValueError, TypeError):
                                    pass

                if pitching_df is not None and not pitching_df.empty:
                    pit_row = None
                    if "Team" in pitching_df.columns:
                        matches = pitching_df[pitching_df["Team"].str.upper() == abbr]
                        if not matches.empty:
                            pit_row = matches.iloc[0]
                    else:
                        try:
                            pit_row = pitching_df.loc[abbr]
                        except KeyError:
                            pass

                    if pit_row is not None:
                        era_raw = pit_row.get("ERA")
                        if era_raw is not None:
                            try:
                                era = round(float(era_raw), 4)
                            except (ValueError, TypeError):
                                pass

                        for col in pitching_df.columns:
                            val = pit_row.get(col)
                            if isinstance(val, (int, float)):
                                try:
                                    fval = float(val)
                                    if fval != fval:  # NaN check
                                        continue
                                    extra_stats[f"pit_{col}"] = round(fval, 4)
                                except (ValueError, TypeError):
                                    pass

                if era is not None:
                    extra_stats["era"] = era
                if ops_val is not None:
                    extra_stats["ops"] = ops_val
                if runs_per_game is not None:
                    extra_stats["runs_per_game"] = runs_per_game

                obj, created = TeamSeasonStats.objects.update_or_create(
                    team=team,
                    season=season_obj,
                    defaults={
                        "games_played": gp,
                        "points_per_game": runs_per_game,
                        "extra_stats": extra_stats,
                    },
                )
                if created:
                    result["created"] += 1
                else:
                    result["updated"] += 1

            except Exception as exc:
                self.logger.error("Error saving MLB team stats for %s: %s", abbr, exc)
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
