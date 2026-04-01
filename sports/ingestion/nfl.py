"""
NFL data ingestor.

Data sources:
- ESPN public API  — teams, live scores, injuries
- nfl_data_py      — historical schedules, team stats
"""

import datetime
import logging

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

logger = logging.getLogger("ingestion.nfl")

# Mapping from ESPN injury status strings to our InjuryStatus choices
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

# NFL teams are all in one league
NFL_LEAGUE_ABBREV = "NFL"


def _get_or_create_nfl_league() -> League:
    league, _ = League.objects.get_or_create(
        sport=Sport.NFL,
        abbreviation=NFL_LEAGUE_ABBREV,
        defaults={"name": "National Football League", "country": "USA"},
    )
    return league


class NFLIngestor(BaseIngestor):
    sport = "NFL"
    ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports/football/nfl"

    # ------------------------------------------------------------------
    # Teams
    # ------------------------------------------------------------------

    def ingest_teams(self) -> dict:
        """Fetch all NFL teams from the ESPN API and upsert them."""
        result = self._empty_result()
        league = _get_or_create_nfl_league()

        url = f"{self.ESPN_BASE}/teams"
        try:
            resp = requests.get(url, params={"limit": 100}, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            self.logger.error("Failed to fetch ESPN teams: %s", exc)
            result["errors"] += 1
            return result

        # ESPN wraps teams under sports[0].leagues[0].teams
        try:
            raw_teams = data["sports"][0]["leagues"][0]["teams"]
        except (KeyError, IndexError):
            # Flat list fallback
            raw_teams = data.get("teams", [])

        for entry in raw_teams:
            team_data = entry.get("team", entry)
            try:
                espn_id = str(team_data.get("id", ""))
                abbreviation = team_data.get("abbreviation", "").upper()
                location = team_data.get("location", "")   # city / region
                nickname = team_data.get("name", "")        # e.g. "Bears"
                display_name = team_data.get("displayName", f"{location} {nickname}".strip())
                venue_info = team_data.get("venue", {})
                venue_name = venue_info.get("fullName", "")

                obj, created = Team.objects.update_or_create(
                    sport=Sport.NFL,
                    abbreviation=abbreviation,
                    defaults={
                        "league": league,
                        "name": nickname,
                        "city": location,
                        "venue": venue_name,
                        "espn_id": espn_id,
                        "is_active": True,
                    },
                )
                if created:
                    result["created"] += 1
                else:
                    result["updated"] += 1
            except Exception as exc:
                self.logger.error("Error upserting team %s: %s", team_data.get("abbreviation"), exc)
                result["errors"] += 1

        self._log_result("ingest_teams", result)
        return result

    # ------------------------------------------------------------------
    # Schedule
    # ------------------------------------------------------------------

    def ingest_schedule(self, season_year: int) -> dict:
        """Import NFL schedule for *season_year* using nfl_data_py."""
        result = self._empty_result()

        try:
            import nfl_data_py as nfl
        except ImportError:
            self.logger.error("nfl_data_py is not installed. Run: pip install nfl_data_py")
            result["errors"] += 1
            return result

        try:
            df = nfl.import_schedules([season_year])
        except Exception as exc:
            self.logger.error("nfl_data_py.import_schedules failed: %s", exc)
            result["errors"] += 1
            return result

        if df is None or df.empty:
            self.logger.warning("No schedule data returned for season %s", season_year)
            return result

        league = _get_or_create_nfl_league()
        season_label = str(season_year)
        season, _ = Season.objects.get_or_create(
            sport=Sport.NFL,
            league=league,
            year=season_year,
            defaults={"label": season_label, "is_current": False},
        )

        for _, row in df.iterrows():
            try:
                game_id = str(row.get("game_id", "")).strip()
                if not game_id:
                    continue

                away_abbr = str(row.get("away_team", "")).upper()
                home_abbr = str(row.get("home_team", "")).upper()
                if not away_abbr or not home_abbr:
                    continue

                try:
                    home_team = Team.objects.get(sport=Sport.NFL, abbreviation=home_abbr)
                    away_team = Team.objects.get(sport=Sport.NFL, abbreviation=away_abbr)
                except Team.DoesNotExist:
                    self.logger.warning(
                        "Team not found: home=%s away=%s — run ingest_teams first",
                        home_abbr,
                        away_abbr,
                    )
                    result["errors"] += 1
                    continue

                # Parse game date
                gameday_raw = row.get("gameday", None)
                if gameday_raw is None:
                    continue
                try:
                    if hasattr(gameday_raw, "date"):
                        game_date = gameday_raw.date()
                    else:
                        game_date = datetime.date.fromisoformat(str(gameday_raw))
                except (ValueError, TypeError):
                    result["errors"] += 1
                    continue

                # Parse game time (stored as HH:MM string in ET)
                gametime_raw = row.get("gametime", None)
                game_time = None
                if gametime_raw and str(gametime_raw) not in ("nan", "None", ""):
                    try:
                        game_time = datetime.time.fromisoformat(str(gametime_raw)[:5])
                    except ValueError:
                        pass

                # Scores
                away_score_raw = row.get("away_score", None)
                home_score_raw = row.get("home_score", None)
                try:
                    away_score = int(away_score_raw) if away_score_raw is not None and str(away_score_raw) != "nan" else None
                    home_score = int(home_score_raw) if home_score_raw is not None and str(home_score_raw) != "nan" else None
                except (ValueError, TypeError):
                    away_score = home_score = None

                # Determine status
                if home_score is not None and away_score is not None:
                    status = GameStatus.FINAL
                else:
                    status = GameStatus.SCHEDULED

                # Rest days
                def _rest(val):
                    try:
                        v = float(val)
                        return int(v) if not __import__("math").isnan(v) else None
                    except (TypeError, ValueError):
                        return None

                away_rest = _rest(row.get("away_rest"))
                home_rest = _rest(row.get("home_rest"))

                week = None
                try:
                    w = row.get("week")
                    if w is not None and str(w) not in ("nan", "None"):
                        week = int(float(w))
                except (ValueError, TypeError):
                    pass

                stadium = str(row.get("stadium", "")) if row.get("stadium") else ""

                obj, created = Game.objects.update_or_create(
                    sport=Sport.NFL,
                    external_id=game_id,
                    defaults={
                        "season": season,
                        "home_team": home_team,
                        "away_team": away_team,
                        "game_date": game_date,
                        "game_time": game_time,
                        "status": status,
                        "home_score": home_score,
                        "away_score": away_score,
                        "venue": stadium,
                        "week": week,
                        "away_rest_days": away_rest,
                        "home_rest_days": home_rest,
                    },
                )
                if created:
                    result["created"] += 1
                else:
                    result["updated"] += 1

            except Exception as exc:
                self.logger.error("Error processing game row %s: %s", row.get("game_id"), exc)
                result["errors"] += 1

        self._log_result("ingest_schedule", result)
        return result

    # ------------------------------------------------------------------
    # Scores
    # ------------------------------------------------------------------

    def ingest_scores(self, game_date=None) -> dict:
        """
        Update scores for games on *game_date* (defaults to today).

        Strategy:
        1. Pull ESPN scoreboard for live/completed scores.
        2. Persist any changes to matching Game objects.
        """
        result = self._empty_result()
        target_date = game_date or self._today()

        if isinstance(target_date, datetime.datetime):
            target_date = target_date.date()

        date_str = target_date.strftime("%Y%m%d")
        url = f"{self.ESPN_BASE}/scoreboard"
        try:
            resp = requests.get(url, params={"dates": date_str}, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            self.logger.error("Failed to fetch ESPN scoreboard for %s: %s", date_str, exc)
            result["errors"] += 1
            return result

        events = data.get("events", [])
        for event in events:
            try:
                espn_event_id = str(event.get("id", ""))
                competitions = event.get("competitions", [])
                if not competitions:
                    continue
                comp = competitions[0]

                competitors = {c["homeAway"]: c for c in comp.get("competitors", [])}
                home_comp = competitors.get("home", {})
                away_comp = competitors.get("away", {})

                home_score_raw = home_comp.get("score", None)
                away_score_raw = away_comp.get("score", None)
                try:
                    home_score = int(home_score_raw) if home_score_raw not in (None, "") else None
                    away_score = int(away_score_raw) if away_score_raw not in (None, "") else None
                except (ValueError, TypeError):
                    home_score = away_score = None

                # Determine status from ESPN status type
                state = event.get("status", {}).get("type", {}).get("state", "pre")
                espn_description = event.get("status", {}).get("type", {}).get("description", "")
                if state == "post":
                    status = GameStatus.FINAL
                elif state == "in":
                    status = GameStatus.IN_PROGRESS
                elif "postponed" in espn_description.lower():
                    status = GameStatus.POSTPONED
                elif "canceled" in espn_description.lower() or "cancelled" in espn_description.lower():
                    status = GameStatus.CANCELLED
                else:
                    status = GameStatus.SCHEDULED

                # Find the matching game — try espn_id first, then external_id prefix
                game = None
                if espn_event_id:
                    game = Game.objects.filter(sport=Sport.NFL, espn_id=espn_event_id).first()
                if game is None:
                    # ESPN event id sometimes matches the nfl_data_py game_id suffix
                    game = Game.objects.filter(
                        sport=Sport.NFL,
                        game_date=target_date,
                    ).filter(
                        home_team__espn_id=str(home_comp.get("id", ""))
                    ).first()

                if game is None:
                    result["errors"] += 1
                    continue

                updated_fields: dict = {"espn_id": espn_event_id, "status": status}
                if home_score is not None:
                    updated_fields["home_score"] = home_score
                if away_score is not None:
                    updated_fields["away_score"] = away_score

                for field, value in updated_fields.items():
                    setattr(game, field, value)
                game.save(update_fields=list(updated_fields.keys()) + ["updated_at"])
                result["updated"] += 1

            except Exception as exc:
                self.logger.error("Error processing ESPN event %s: %s", event.get("id"), exc)
                result["errors"] += 1

        self._log_result("ingest_scores", result)
        return result

    # ------------------------------------------------------------------
    # Injuries
    # ------------------------------------------------------------------

    def ingest_injuries(self) -> dict:
        """Fetch the ESPN NFL injury feed and upsert InjuryReport objects."""
        result = self._empty_result()

        url = f"{self.ESPN_BASE}/injuries"
        try:
            resp = requests.get(url, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            self.logger.error("Failed to fetch ESPN injuries: %s", exc)
            result["errors"] += 1
            return result

        today = self._today()

        # ESPN returns a list of teams, each with an "injuries" list
        for team_entry in data:
            team_data = team_entry.get("team", {})
            espn_team_id = str(team_data.get("id", ""))
            team = Team.objects.filter(sport=Sport.NFL, espn_id=espn_team_id).first()

            for injury in team_entry.get("injuries", []):
                try:
                    athlete = injury.get("athlete", {})
                    athlete_id = str(athlete.get("id", ""))
                    athlete_name = athlete.get("displayName", "")

                    # Get or create player
                    player = None
                    if athlete_id:
                        player = Player.objects.filter(sport=Sport.NFL, espn_id=athlete_id).first()
                    if player is None and athlete_name:
                        player, p_created = Player.objects.get_or_create(
                            sport=Sport.NFL,
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
                        "Error processing injury for %s: %s",
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
        """Import NFL team-level stats via nfl_data_py and store in TeamSeasonStats."""
        result = self._empty_result()

        try:
            import nfl_data_py as nfl
        except ImportError:
            self.logger.error("nfl_data_py is not installed. Run: pip install nfl_data_py")
            result["errors"] += 1
            return result

        try:
            df = nfl.import_team_stats([season_year])
        except Exception as exc:
            self.logger.error("nfl_data_py.import_team_stats failed: %s", exc)
            result["errors"] += 1
            return result

        if df is None or df.empty:
            self.logger.warning("No team stats returned for season %s", season_year)
            return result

        league = _get_or_create_nfl_league()
        season, _ = Season.objects.get_or_create(
            sport=Sport.NFL,
            league=league,
            year=season_year,
            defaults={"label": str(season_year), "is_current": False},
        )

        # nfl_data_py team_stats are per-game; aggregate by team
        numeric_cols = [c for c in df.columns if df[c].dtype in ("float64", "int64")]

        for team_abbr, group in df.groupby("team"):
            try:
                team_abbr_upper = str(team_abbr).upper()
                team = Team.objects.filter(sport=Sport.NFL, abbreviation=team_abbr_upper).first()
                if team is None:
                    self.logger.warning("Team not found for abbrev %s", team_abbr_upper)
                    result["errors"] += 1
                    continue

                aggregated = group[numeric_cols].mean(numeric_only=True).to_dict()
                # Round floats for cleanliness
                extra_stats = {k: round(v, 4) if isinstance(v, float) else v for k, v in aggregated.items()}

                games_played = int(len(group))
                obj, created = TeamSeasonStats.objects.update_or_create(
                    team=team,
                    season=season,
                    defaults={
                        "games_played": games_played,
                        "extra_stats": extra_stats,
                    },
                )
                if created:
                    result["created"] += 1
                else:
                    result["updated"] += 1

            except Exception as exc:
                self.logger.error("Error saving team stats for %s: %s", team_abbr, exc)
                result["errors"] += 1

        self._log_result("ingest_team_stats", result)
        return result

    # ------------------------------------------------------------------
    # Orchestration override — include team stats
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
