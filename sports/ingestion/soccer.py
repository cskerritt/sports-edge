"""
Soccer data ingestor.

Data source: football-data.org v4 API.

Requires FOOTBALL_DATA_API_KEY in Django settings (free-tier key works for
Premier League, La Liga, Bundesliga, Serie A, and Champions League).
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

# Competition codes → (display name, country) and football-data.org numeric IDs
COMPETITION_IDS: dict[str, tuple[str, str]] = {
    "PL": ("Premier League", "England"),
    "PD": ("La Liga", "Spain"),
    "BL1": ("Bundesliga", "Germany"),
    "SA": ("Serie A", "Italy"),
    "CL": ("Champions League", "Europe"),
}

COMPETITION_FD_IDS: dict[str, int] = {
    "PL": 2021,
    "PD": 2014,
    "BL1": 2002,
    "SA": 2019,
    "CL": 2001,
}

# football-data.org match status → GameStatus
FD_STATUS_MAP: dict[str, str] = {
    "SCHEDULED": GameStatus.SCHEDULED,
    "TIMED": GameStatus.SCHEDULED,
    "IN_PLAY": GameStatus.IN_PROGRESS,
    "PAUSED": GameStatus.IN_PROGRESS,
    "FINISHED": GameStatus.FINAL,
    "AWARDED": GameStatus.FINAL,
    "POSTPONED": GameStatus.POSTPONED,
    "SUSPENDED": GameStatus.POSTPONED,
    "CANCELLED": GameStatus.CANCELLED,
}

# Pause between requests to respect free-tier rate limits (10 req/min)
FD_REQUEST_SLEEP = 6.5


class SoccerIngestor(BaseIngestor):
    sport = "SOCCER"
    BASE = "https://api.football-data.org/v4"

    def __init__(self):
        super().__init__()
        from django.conf import settings
        self.api_key: str = getattr(settings, "FOOTBALL_DATA_API_KEY", "") or ""
        self.headers: dict = {"X-Auth-Token": self.api_key} if self.api_key else {}
        if not self.api_key:
            self.logger.warning(
                "FOOTBALL_DATA_API_KEY is not set — requests may be rejected or rate-limited."
            )

    # ------------------------------------------------------------------
    # Internal HTTP helper
    # ------------------------------------------------------------------

    def _get(self, path: str, params: dict | None = None) -> dict | list | None:
        """
        GET *path* (relative to BASE) with auth headers.
        Sleeps before the request to avoid 429s on the free tier.
        Returns parsed JSON or None on error.
        """
        time.sleep(FD_REQUEST_SLEEP)
        url = f"{self.BASE}{path}"
        try:
            resp = requests.get(url, headers=self.headers, params=params or {}, timeout=20)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 429:
                self.logger.warning("Rate-limited by football-data.org — sleeping 60s")
                time.sleep(60)
                # One retry
                try:
                    resp = requests.get(url, headers=self.headers, params=params or {}, timeout=20)
                    resp.raise_for_status()
                    return resp.json()
                except Exception as retry_exc:
                    self.logger.error("Retry after 429 also failed for %s: %s", url, retry_exc)
                    return None
            self.logger.error("HTTP error for %s: %s", url, exc)
            return None
        except Exception as exc:
            self.logger.error("Request failed for %s: %s", url, exc)
            return None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_or_create_league(self, code: str) -> League:
        name, country = COMPETITION_IDS[code]
        fd_id = COMPETITION_FD_IDS[code]
        league, _ = League.objects.get_or_create(
            sport=Sport.SOCCER,
            abbreviation=code,
            defaults={
                "name": name,
                "country": country,
                "external_id": str(fd_id),
            },
        )
        return league

    def _get_or_create_season(self, league: League, season_year: int) -> Season:
        label = f"{season_year}/{str(season_year + 1)[-2:]}"
        season, _ = Season.objects.get_or_create(
            sport=Sport.SOCCER,
            league=league,
            year=season_year,
            defaults={"label": label, "is_current": False},
        )
        return season

    # ------------------------------------------------------------------
    # Teams
    # ------------------------------------------------------------------

    def ingest_teams(self) -> dict:
        """
        For each competition, GET /competitions/{id}/teams and upsert Team objects.
        Teams are associated with the competition's League.
        """
        result = self._empty_result()

        for code, fd_id in COMPETITION_FD_IDS.items():
            comp_name, country = COMPETITION_IDS[code]
            league = self._get_or_create_league(code)

            data = self._get(f"/competitions/{fd_id}/teams")
            if data is None:
                result["errors"] += 1
                continue

            for t in data.get("teams", []):
                try:
                    team_id = t.get("id")
                    name = t.get("shortName") or t.get("name", "")
                    tla = (t.get("tla") or name[:10]).upper()
                    area = t.get("area", {}).get("name", country)
                    venue = t.get("venue", "")

                    if not tla:
                        continue

                    # For soccer, uniqueness is (sport, abbreviation) but TLAs can collide
                    # across competitions — include league FK in defaults.
                    obj, created = Team.objects.update_or_create(
                        sport=Sport.SOCCER,
                        abbreviation=tla,
                        defaults={
                            "league": league,
                            "name": name,
                            "city": area,
                            "venue": venue,
                            "football_data_id": team_id,
                            "is_active": True,
                        },
                    )
                    if created:
                        result["created"] += 1
                    else:
                        result["updated"] += 1

                except Exception as exc:
                    self.logger.error(
                        "Error upserting soccer team %s in %s: %s", t.get("name"), code, exc
                    )
                    result["errors"] += 1

        self._log_result("ingest_teams", result)
        return result

    # ------------------------------------------------------------------
    # Schedule
    # ------------------------------------------------------------------

    def ingest_schedule(self, season_year: int) -> dict:
        """
        For each competition, fetch all matches for *season_year* and upsert Game objects.
        """
        result = self._empty_result()

        for code, fd_id in COMPETITION_FD_IDS.items():
            league = self._get_or_create_league(code)
            season_obj = self._get_or_create_season(league, season_year)

            data = self._get(
                f"/competitions/{fd_id}/matches",
                params={"season": season_year},
            )
            if data is None:
                result["errors"] += 1
                continue

            for match in data.get("matches", []):
                try:
                    match_id = str(match.get("id", ""))
                    if not match_id:
                        continue

                    home_team_data = match.get("homeTeam", {})
                    away_team_data = match.get("awayTeam", {})
                    home_fd_id = home_team_data.get("id")
                    away_fd_id = away_team_data.get("id")

                    try:
                        home_team = Team.objects.get(sport=Sport.SOCCER, football_data_id=home_fd_id)
                        away_team = Team.objects.get(sport=Sport.SOCCER, football_data_id=away_fd_id)
                    except Team.DoesNotExist:
                        home_tla = (home_team_data.get("tla") or "").upper()
                        away_tla = (away_team_data.get("tla") or "").upper()
                        self.logger.warning(
                            "Soccer team not found by FD id: home=%s (%s) away=%s (%s) — run ingest_teams first",
                            home_fd_id, home_tla, away_fd_id, away_tla,
                        )
                        result["errors"] += 1
                        continue

                    utc_date_raw = match.get("utcDate", "")
                    try:
                        dt = datetime.datetime.fromisoformat(utc_date_raw.replace("Z", "+00:00"))
                        game_date = dt.date()
                        game_time = dt.time()
                    except (ValueError, TypeError):
                        result["errors"] += 1
                        continue

                    fd_status = match.get("status", "SCHEDULED")
                    status = FD_STATUS_MAP.get(fd_status, GameStatus.SCHEDULED)

                    score_data = match.get("score", {})
                    full_time = score_data.get("fullTime", {})
                    home_score_raw = full_time.get("home")
                    away_score_raw = full_time.get("away")
                    try:
                        home_score = int(home_score_raw) if home_score_raw is not None else None
                        away_score = int(away_score_raw) if away_score_raw is not None else None
                    except (ValueError, TypeError):
                        home_score = away_score = None

                    # Matchday = week equivalent
                    matchday = match.get("matchday")
                    week = int(matchday) if matchday else None

                    obj, created = Game.objects.update_or_create(
                        sport=Sport.SOCCER,
                        external_id=match_id,
                        defaults={
                            "season": season_obj,
                            "home_team": home_team,
                            "away_team": away_team,
                            "game_date": game_date,
                            "game_time": game_time,
                            "status": status,
                            "home_score": home_score,
                            "away_score": away_score,
                            "week": week,
                        },
                    )
                    if created:
                        result["created"] += 1
                    else:
                        result["updated"] += 1

                except Exception as exc:
                    self.logger.error(
                        "Error processing soccer match %s in %s: %s",
                        match.get("id"), code, exc,
                    )
                    result["errors"] += 1

        self._log_result("ingest_schedule", result)
        return result

    # ------------------------------------------------------------------
    # Scores
    # ------------------------------------------------------------------

    def ingest_scores(self, game_date=None) -> dict:
        """
        Fetch matches for *game_date* across all competitions using the
        /matches?date= endpoint and update Game scores / status.
        """
        result = self._empty_result()

        target_date = game_date or self._today()
        if isinstance(target_date, datetime.datetime):
            target_date = target_date.date()

        date_str = target_date.strftime("%Y-%m-%d")

        data = self._get("/matches", params={"date": date_str})
        if data is None:
            result["errors"] += 1
            return result

        for match in data.get("matches", []):
            try:
                match_id = str(match.get("id", ""))
                if not match_id:
                    continue

                fd_status = match.get("status", "SCHEDULED")
                status = FD_STATUS_MAP.get(fd_status, GameStatus.SCHEDULED)

                score_data = match.get("score", {})
                full_time = score_data.get("fullTime", {})
                home_score_raw = full_time.get("home")
                away_score_raw = full_time.get("away")
                try:
                    home_score = int(home_score_raw) if home_score_raw is not None else None
                    away_score = int(away_score_raw) if away_score_raw is not None else None
                except (ValueError, TypeError):
                    home_score = away_score = None

                game_obj = Game.objects.filter(sport=Sport.SOCCER, external_id=match_id).first()
                if game_obj is None:
                    result["errors"] += 1
                    continue

                update_kwargs: dict = {"status": status}
                if home_score is not None:
                    update_kwargs["home_score"] = home_score
                if away_score is not None:
                    update_kwargs["away_score"] = away_score

                for k, v in update_kwargs.items():
                    setattr(game_obj, k, v)
                game_obj.save(update_fields=list(update_kwargs.keys()) + ["updated_at"])
                result["updated"] += 1

            except Exception as exc:
                self.logger.error(
                    "Error updating soccer score for match %s: %s", match.get("id"), exc
                )
                result["errors"] += 1

        self._log_result("ingest_scores", result)
        return result

    # ------------------------------------------------------------------
    # Injuries — not available in free tier
    # ------------------------------------------------------------------

    def ingest_injuries(self) -> dict:
        """
        Injury data is not available in the football-data.org free tier.
        Logs a warning and returns an empty result.
        """
        self.logger.warning(
            "[SOCCER] Injury data is not available in the football-data.org free tier. "
            "Upgrade to a paid plan or integrate a separate injury feed."
        )
        return self._empty_result()

    # ------------------------------------------------------------------
    # Team standings / stats
    # ------------------------------------------------------------------

    def ingest_team_stats(self, season_year: int) -> dict:
        """
        Fetch standings for each competition and store win/draw/loss and
        goals data in TeamSeasonStats.
        """
        result = self._empty_result()

        for code, fd_id in COMPETITION_FD_IDS.items():
            league = self._get_or_create_league(code)
            season_obj = self._get_or_create_season(league, season_year)

            data = self._get(
                f"/competitions/{fd_id}/standings",
                params={"season": season_year},
            )
            if data is None:
                result["errors"] += 1
                continue

            for standing_group in data.get("standings", []):
                for entry in standing_group.get("table", []):
                    try:
                        team_data = entry.get("team", {})
                        fd_team_id = team_data.get("id")

                        team = Team.objects.filter(
                            sport=Sport.SOCCER, football_data_id=fd_team_id
                        ).first()
                        if team is None:
                            tla = (team_data.get("tla") or "").upper()
                            team = Team.objects.filter(sport=Sport.SOCCER, abbreviation=tla).first()
                        if team is None:
                            self.logger.warning(
                                "Soccer team not found for fd_id %s in standings", fd_team_id
                            )
                            result["errors"] += 1
                            continue

                        gp = int(entry.get("playedGames", 0) or 0)
                        wins = int(entry.get("won", 0) or 0)
                        draws = int(entry.get("draw", 0) or 0)
                        losses = int(entry.get("lost", 0) or 0)
                        goals_for = entry.get("goalsFor", 0) or 0
                        goals_against = entry.get("goalsAgainst", 0) or 0
                        goal_diff = entry.get("goalDifference", 0) or 0
                        points = entry.get("points", 0) or 0

                        gpg = round(goals_for / gp, 4) if gp else None
                        gapg = round(goals_against / gp, 4) if gp else None

                        extra_stats = {
                            "goals_for": goals_for,
                            "goals_against": goals_against,
                            "goal_difference": goal_diff,
                            "table_points": points,
                            "competition_code": code,
                            "standing_type": standing_group.get("type", ""),
                        }

                        obj, created = TeamSeasonStats.objects.update_or_create(
                            team=team,
                            season=season_obj,
                            defaults={
                                "games_played": gp,
                                "wins": wins,
                                "draws": draws,
                                "losses": losses,
                                "points_per_game": gpg,
                                "points_allowed_per_game": gapg,
                                "extra_stats": extra_stats,
                            },
                        )
                        if created:
                            result["created"] += 1
                        else:
                            result["updated"] += 1

                    except Exception as exc:
                        self.logger.error(
                            "Error saving soccer team stats for %s in %s: %s",
                            entry.get("team", {}).get("name"), code, exc,
                        )
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
