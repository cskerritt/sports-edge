"""
NBA data ingestor.

Data sources:
- nba_api (stats.nba.com)   — teams, schedule, live scores, team stats
- ESPN public API            — injuries
"""

import datetime
import time

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

ESPN_NBA_BASE = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"

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

# nba_api rate-limit guard
NBA_API_SLEEP = 0.6

NBA_GAME_STATUS_MAP = {
    1: GameStatus.SCHEDULED,
    2: GameStatus.IN_PROGRESS,
    3: GameStatus.FINAL,
}


def _nba_season_string(season_year: int) -> str:
    """Convert 2024 → '2024-25'."""
    return f"{season_year}-{str(season_year + 1)[-2:]}"


def _get_or_create_nba_league() -> League:
    league, _ = League.objects.get_or_create(
        sport=Sport.NBA,
        abbreviation="NBA",
        defaults={"name": "National Basketball Association", "country": "USA"},
    )
    return league


class NBAIngestor(BaseIngestor):
    sport = "NBA"

    # ------------------------------------------------------------------
    # Teams
    # ------------------------------------------------------------------

    def ingest_teams(self) -> dict:
        """Upsert all NBA teams using nba_api static data."""
        result = self._empty_result()

        try:
            from nba_api.stats.static import teams as nba_teams_static
        except ImportError:
            self.logger.error("nba_api is not installed. Run: pip install nba_api")
            result["errors"] += 1
            return result

        league = _get_or_create_nba_league()

        try:
            raw_teams = nba_teams_static.get_teams()
        except Exception as exc:
            self.logger.error("nba_api.stats.static.teams.get_teams() failed: %s", exc)
            result["errors"] += 1
            return result

        for t in raw_teams:
            try:
                # Static data keys: id, full_name, abbreviation, nickname, city, state, year_founded
                nba_id = t.get("id")
                abbreviation = t.get("abbreviation", "").upper()
                nickname = t.get("nickname", "")  # e.g. "Bulls"
                city = t.get("city", "")
                full_name = t.get("full_name", f"{city} {nickname}".strip())

                obj, created = Team.objects.update_or_create(
                    sport=Sport.NBA,
                    abbreviation=abbreviation,
                    defaults={
                        "league": league,
                        "name": nickname,
                        "city": city,
                        "nba_api_id": nba_id,
                        "is_active": True,
                    },
                )
                if created:
                    result["created"] += 1
                else:
                    result["updated"] += 1
            except Exception as exc:
                self.logger.error("Error upserting NBA team %s: %s", t.get("abbreviation"), exc)
                result["errors"] += 1

        # Backfill ESPN IDs from the ESPN API so ESPN scoreboard lookups work
        self._backfill_espn_ids(league)

        self._log_result("ingest_teams", result)
        return result

    def _backfill_espn_ids(self, league):
        """Fetch ESPN team data and set espn_id on existing Team records."""
        url = f"{ESPN_NBA_BASE}/teams"
        try:
            resp = requests.get(url, params={"limit": 100}, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            self.logger.warning("ESPN NBA teams fetch failed (espn_id backfill): %s", exc)
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
                sport=Sport.NBA, abbreviation=abbreviation, espn_id=""
            ).update(espn_id=espn_id)

    # ------------------------------------------------------------------
    # Schedule
    # ------------------------------------------------------------------

    def ingest_schedule(self, season_year: int) -> dict:
        """
        Import the NBA schedule for *season_year* (e.g. 2024 → 2024-25 season)
        using LeagueGameFinder from nba_api.
        """
        result = self._empty_result()

        try:
            from nba_api.stats.endpoints import leaguegamefinder
        except ImportError:
            self.logger.error("nba_api is not installed. Run: pip install nba_api")
            result["errors"] += 1
            return result

        league = _get_or_create_nba_league()
        season_str = _nba_season_string(season_year)

        season_label = season_str
        season_obj, _ = Season.objects.get_or_create(
            sport=Sport.NBA,
            league=league,
            year=season_year,
            defaults={"label": season_label, "is_current": False},
        )

        try:
            time.sleep(NBA_API_SLEEP)
            finder = leaguegamefinder.LeagueGameFinder(
                season_nullable=season_str,
                league_id_nullable="00",  # NBA
                season_type_nullable="Regular Season",
            )
            df = finder.get_data_frames()[0]
        except Exception as exc:
            self.logger.error("LeagueGameFinder failed for %s: %s", season_str, exc)
            result["errors"] += 1
            return result

        if df is None or df.empty:
            self.logger.warning("No schedule data returned for NBA %s", season_str)
            return result

        # LeagueGameFinder returns one row per team per game (home and away rows share GAME_ID)
        # Deduplicate by GAME_ID, keeping one row per game from each team pairing.
        seen_game_ids: set = set()

        for _, row in df.iterrows():
            try:
                game_id = str(row.get("GAME_ID", "")).strip()
                if not game_id or game_id in seen_game_ids:
                    continue
                seen_game_ids.add(game_id)

                # MATCHUP format: "LAL vs. GSW" (home) or "LAL @ GSW" (away)
                matchup = str(row.get("MATCHUP", ""))
                team_abbr = str(row.get("TEAM_ABBREVIATION", "")).upper()

                if " vs. " in matchup:
                    opponent_abbr = matchup.split(" vs. ")[1].strip().upper()
                    home_abbr, away_abbr = team_abbr, opponent_abbr
                elif " @ " in matchup:
                    opponent_abbr = matchup.split(" @ ")[1].strip().upper()
                    home_abbr, away_abbr = opponent_abbr, team_abbr
                else:
                    result["errors"] += 1
                    continue

                try:
                    home_team = Team.objects.get(sport=Sport.NBA, abbreviation=home_abbr)
                    away_team = Team.objects.get(sport=Sport.NBA, abbreviation=away_abbr)
                except Team.DoesNotExist:
                    self.logger.warning(
                        "NBA team not found: home=%s away=%s", home_abbr, away_abbr
                    )
                    result["errors"] += 1
                    continue

                game_date_raw = row.get("GAME_DATE", None)
                try:
                    if hasattr(game_date_raw, "date"):
                        game_date = game_date_raw.date()
                    else:
                        game_date = datetime.date.fromisoformat(str(game_date_raw))
                except (ValueError, TypeError):
                    result["errors"] += 1
                    continue

                wl = str(row.get("WL", "")) if row.get("WL") else ""

                # Scores — GameFinder gives us the current team's PTS; we need opponent too.
                # We must look for the matching row with the same GAME_ID for the other team.
                # Since we're iterating once, approximate: store what we have.
                pts_for = row.get("PTS", None)
                try:
                    pts_for = int(pts_for) if pts_for is not None else None
                except (ValueError, TypeError):
                    pts_for = None

                # Determine status
                if wl in ("W", "L"):
                    status = GameStatus.FINAL
                else:
                    status = GameStatus.SCHEDULED

                # We can set partial score now; the scores ingest will complete it.
                if team_abbr == home_abbr:
                    home_score = pts_for
                    away_score = None
                else:
                    away_score = pts_for
                    home_score = None

                obj, created = Game.objects.update_or_create(
                    sport=Sport.NBA,
                    external_id=game_id,
                    defaults={
                        "season": season_obj,
                        "home_team": home_team,
                        "away_team": away_team,
                        "game_date": game_date,
                        "status": status,
                        "home_score": home_score,
                        "away_score": away_score,
                    },
                )
                if created:
                    result["created"] += 1
                else:
                    result["updated"] += 1

            except Exception as exc:
                self.logger.error("Error processing NBA game row: %s", exc)
                result["errors"] += 1

        # Second pass: fill in opponent scores for final games using the counterpart rows
        self._fill_opponent_scores(df, result)

        self._log_result("ingest_schedule", result)
        return result

    def _fill_opponent_scores(self, df, result: dict) -> None:
        """
        LeagueGameFinder only gives each team's own score per row.
        Group by GAME_ID and pair home/away scores.
        """
        try:
            for game_id, group in df.groupby("GAME_ID"):
                if len(group) < 2:
                    continue
                game_id_str = str(game_id)
                game_obj = Game.objects.filter(sport=Sport.NBA, external_id=game_id_str).first()
                if game_obj is None:
                    continue

                scores: dict = {}
                for _, row in group.iterrows():
                    matchup = str(row.get("MATCHUP", ""))
                    abbr = str(row.get("TEAM_ABBREVIATION", "")).upper()
                    pts = row.get("PTS", None)
                    try:
                        pts = int(pts) if pts is not None else None
                    except (ValueError, TypeError):
                        pts = None

                    if " vs. " in matchup:
                        scores["home"] = pts
                    elif " @ " in matchup:
                        scores["away"] = pts

                update_kwargs = {}
                if "home" in scores:
                    update_kwargs["home_score"] = scores["home"]
                if "away" in scores:
                    update_kwargs["away_score"] = scores["away"]

                if update_kwargs:
                    for k, v in update_kwargs.items():
                        setattr(game_obj, k, v)
                    game_obj.save(update_fields=list(update_kwargs.keys()) + ["updated_at"])

        except Exception as exc:
            self.logger.error("Error during _fill_opponent_scores: %s", exc)
            result["errors"] += 1

    # ------------------------------------------------------------------
    # Scores
    # ------------------------------------------------------------------

    def ingest_scores(self, game_date=None) -> dict:
        """Fetch today's NBA games from ESPN scoreboard.

        Uses the shared ESPN scoreboard method which creates games that
        don't exist yet and updates scores for games that do.
        """
        return self.ingest_espn_scoreboard(game_date=game_date)

    # ------------------------------------------------------------------
    # Injuries
    # ------------------------------------------------------------------

    def ingest_injuries(self) -> dict:
        """Fetch NBA injuries from ESPN API and upsert InjuryReport objects."""
        result = self._empty_result()
        today = self._today()

        url = f"{ESPN_NBA_BASE}/injuries"
        try:
            resp = requests.get(url, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            self.logger.error("Failed to fetch ESPN NBA injuries: %s", exc)
            result["errors"] += 1
            return result

        for team_entry in self._extract_espn_injury_teams(data):
            team_data = team_entry.get("team", {})
            espn_team_id = str(team_data.get("id", ""))
            team = Team.objects.filter(sport=Sport.NBA, espn_id=espn_team_id).first()

            for injury in team_entry.get("injuries", []):
                try:
                    athlete = injury.get("athlete", {})
                    athlete_id = str(athlete.get("id", ""))
                    athlete_name = athlete.get("displayName", "")

                    player = Player.objects.filter(sport=Sport.NBA, espn_id=athlete_id).first()
                    if player is None and athlete_name:
                        player, _ = Player.objects.get_or_create(
                            sport=Sport.NBA,
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
                        "Error processing NBA injury for %s: %s",
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
        Fetch offensive/defensive ratings and pace via LeagueDashTeamStats and
        store them in TeamSeasonStats.
        """
        result = self._empty_result()

        try:
            from nba_api.stats.endpoints import leaguedashteamstats
        except ImportError:
            self.logger.error("nba_api is not installed.")
            result["errors"] += 1
            return result

        league = _get_or_create_nba_league()
        season_str = _nba_season_string(season_year)
        season_obj, _ = Season.objects.get_or_create(
            sport=Sport.NBA,
            league=league,
            year=season_year,
            defaults={"label": season_str, "is_current": False},
        )

        # Fetch base stats
        try:
            time.sleep(NBA_API_SLEEP)
            base_stats = leaguedashteamstats.LeagueDashTeamStats(
                season=season_str,
                measure_type_detailed_defense="Base",
                per_mode_detailed="PerGame",
            )
            base_df = base_stats.get_data_frames()[0]
        except Exception as exc:
            self.logger.error("LeagueDashTeamStats (Base) failed: %s", exc)
            result["errors"] += 1
            return result

        # Fetch advanced stats (ratings, pace)
        try:
            time.sleep(NBA_API_SLEEP)
            adv_stats = leaguedashteamstats.LeagueDashTeamStats(
                season=season_str,
                measure_type_detailed_defense="Advanced",
                per_mode_detailed="PerGame",
            )
            adv_df = adv_stats.get_data_frames()[0]
        except Exception as exc:
            self.logger.error("LeagueDashTeamStats (Advanced) failed: %s", exc)
            adv_df = None

        for _, row in base_df.iterrows():
            try:
                nba_team_id = row.get("TEAM_ID")
                team = Team.objects.filter(sport=Sport.NBA, nba_api_id=nba_team_id).first()
                if team is None:
                    abbr = str(row.get("TEAM_ABBREVIATION", "")).upper()
                    team = Team.objects.filter(sport=Sport.NBA, abbreviation=abbr).first()
                if team is None:
                    result["errors"] += 1
                    continue

                gp = int(row.get("GP", 0) or 0)
                wins = int(row.get("W", 0) or 0)
                losses = int(row.get("L", 0) or 0)
                pts_pg = float(row.get("PTS", 0) or 0)
                pts_allowed_pg = None  # available in advanced or opponent stats

                extra: dict = {}
                # Collect all numeric columns as extra stats
                for col in base_df.columns:
                    val = row.get(col)
                    if isinstance(val, (int, float)) and col not in ("TEAM_ID",):
                        try:
                            extra[col] = round(float(val), 4)
                        except (ValueError, TypeError):
                            pass

                # Merge advanced stats if available
                off_rating = def_rating = pace = None
                if adv_df is not None:
                    adv_row = adv_df[adv_df["TEAM_ID"] == nba_team_id]
                    if not adv_row.empty:
                        adv = adv_row.iloc[0]
                        off_rating = float(adv.get("OFF_RATING", 0) or 0) or None
                        def_rating = float(adv.get("DEF_RATING", 0) or 0) or None
                        pace = float(adv.get("PACE", 0) or 0) or None
                        pts_allowed_pg = float(adv.get("OPP_PTS", 0) or 0) or None
                        for col in adv_df.columns:
                            val = adv.get(col)
                            if isinstance(val, (int, float)):
                                try:
                                    extra[f"adv_{col}"] = round(float(val), 4)
                                except (ValueError, TypeError):
                                    pass

                obj, created = TeamSeasonStats.objects.update_or_create(
                    team=team,
                    season=season_obj,
                    defaults={
                        "games_played": gp,
                        "wins": wins,
                        "losses": losses,
                        "points_per_game": pts_pg,
                        "points_allowed_per_game": pts_allowed_pg,
                        "offensive_rating": off_rating,
                        "defensive_rating": def_rating,
                        "pace": pace,
                        "extra_stats": extra,
                    },
                )
                if created:
                    result["created"] += 1
                else:
                    result["updated"] += 1

            except Exception as exc:
                self.logger.error("Error saving NBA team stats for team_id %s: %s", row.get("TEAM_ID"), exc)
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
