import datetime
import logging
from datetime import date

import requests

from sports.models import Game, GameStatus, League, Season, Team


# ESPN sport slugs for scoreboard endpoints
ESPN_SPORT_SLUGS = {
    "NFL": "football/nfl",
    "NBA": "basketball/nba",
    "NHL": "hockey/nhl",
    "MLB": "baseball/mlb",
    "SOCCER": "soccer/eng.1",
}

ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports"

# ESPN sometimes uses different abbreviations than nba_api / other sources.
# Map ESPN abbreviation → DB abbreviation for known mismatches.
ESPN_ABBR_ALIASES = {
    "GS": "GSW",     # Golden State Warriors
    "NO": "NOP",     # New Orleans Pelicans
    "SA": "SAS",     # San Antonio Spurs
    "WSH": "WAS",    # Washington (sometimes)
    "PHO": "PHX",    # Phoenix (sometimes)
    "UTAH": "UTA",   # Utah Jazz
    "NY": "NYK",     # New York Knicks (sometimes)
}


class BaseIngestor:
    """
    Abstract base class for all sport-specific data ingestors.

    Subclasses must define ``sport`` as a class-level attribute matching one of
    the ``Sport`` TextChoices values (e.g. "NFL", "NBA", ...).
    """

    sport: str = ""

    def __init__(self):
        if not self.sport:
            raise NotImplementedError("Subclasses must define a non-empty `sport` attribute.")
        self.logger = logging.getLogger(f"ingestion.{self.sport.lower()}")
        self.created = 0
        self.updated = 0
        self.errors = 0

    # ------------------------------------------------------------------
    # Methods that subclasses must implement
    # ------------------------------------------------------------------

    def ingest_teams(self) -> dict:
        """Fetch and upsert all teams for this sport.

        Returns:
            dict with keys ``created``, ``updated``, ``errors``.
        """
        raise NotImplementedError

    def ingest_schedule(self, season_year: int) -> dict:
        """Fetch and upsert the full game schedule for *season_year*.

        Args:
            season_year: The four-digit starting year of the season
                         (e.g. 2024 for the 2024-25 NBA season).

        Returns:
            dict with keys ``created``, ``updated``, ``errors``.
        """
        raise NotImplementedError

    def ingest_scores(self, game_date=None) -> dict:
        """Fetch and update scores for games on *game_date* (defaults to today).

        Args:
            game_date: A ``datetime.date`` instance or ``None`` for today.

        Returns:
            dict with keys ``created``, ``updated``, ``errors``.
        """
        raise NotImplementedError

    def ingest_injuries(self) -> dict:
        """Fetch and upsert current injury reports.

        Returns:
            dict with keys ``created``, ``updated``, ``errors``.
        """
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Convenience orchestration
    # ------------------------------------------------------------------

    def run_full_ingest(self, season_year: int) -> dict:
        """Run all ingest operations in logical order and aggregate results.

        Args:
            season_year: Passed through to schedule/stats methods.

        Returns:
            dict mapping operation name to its result dict.
        """
        results: dict = {}

        self.logger.info("[%s] Starting full ingest for season %s", self.sport, season_year)

        for op_name, op_fn, op_kwargs in [
            ("ingest_teams", self.ingest_teams, {}),
            ("ingest_schedule", self.ingest_schedule, {"season_year": season_year}),
            ("ingest_scores", self.ingest_scores, {}),
            ("ingest_injuries", self.ingest_injuries, {}),
        ]:
            try:
                result = op_fn(**op_kwargs)
            except Exception as exc:
                self.logger.exception("[%s] Unhandled error in %s: %s", self.sport, op_name, exc)
                result = {"created": 0, "updated": 0, "errors": 1}

            self._log_result(op_name, result)
            results[op_name] = result

        self.logger.info("[%s] Full ingest complete: %s", self.sport, results)
        return results

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _log_result(self, operation: str, result: dict) -> None:
        """Log a standardised summary line for a completed operation."""
        created = result.get("created", 0)
        updated = result.get("updated", 0)
        errors = result.get("errors", 0)
        level = logging.WARNING if errors else logging.INFO
        self.logger.log(
            level,
            "[%s] %s — created=%d updated=%d errors=%d",
            self.sport,
            operation,
            created,
            updated,
            errors,
        )

    @staticmethod
    def _empty_result() -> dict:
        """Return a zeroed result dict."""
        return {"created": 0, "updated": 0, "errors": 0}

    @staticmethod
    def _today() -> date:
        """Return today's date in the project timezone (America/New_York)."""
        from django.utils import timezone
        return timezone.localdate()

    def ingest_espn_scoreboard(self, game_date=None) -> dict:
        """Fetch today's games from ESPN and create/update Game records.

        This is the primary way to ensure today's games exist in the database.
        ESPN returns scheduled, in-progress, and completed games.  Games that
        don't yet exist in the DB are **created** so the dashboard can display
        them immediately.

        Works for NFL, NBA, NHL, MLB.
        """
        result = self._empty_result()
        target_date = game_date or self._today()
        if isinstance(target_date, datetime.datetime):
            target_date = target_date.date()

        slug = ESPN_SPORT_SLUGS.get(self.sport)
        if not slug:
            return result

        date_str = target_date.strftime("%Y%m%d")
        url = f"{ESPN_BASE}/{slug}/scoreboard"

        try:
            resp = requests.get(url, params={"dates": date_str}, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            self.logger.error("ESPN scoreboard fetch failed for %s: %s", date_str, exc)
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

                home_espn_id = str(home_comp.get("id", ""))
                away_espn_id = str(away_comp.get("id", ""))
                home_abbr = home_comp.get("team", {}).get("abbreviation", "").upper()
                away_abbr = away_comp.get("team", {}).get("abbreviation", "").upper()

                # Normalize ESPN abbreviation mismatches
                home_abbr = ESPN_ABBR_ALIASES.get(home_abbr, home_abbr)
                away_abbr = ESPN_ABBR_ALIASES.get(away_abbr, away_abbr)

                # Resolve teams
                home_team = (
                    Team.objects.filter(sport=self.sport, espn_id=home_espn_id).first()
                    or Team.objects.filter(sport=self.sport, abbreviation=home_abbr).first()
                )
                away_team = (
                    Team.objects.filter(sport=self.sport, espn_id=away_espn_id).first()
                    or Team.objects.filter(sport=self.sport, abbreviation=away_abbr).first()
                )

                if not home_team or not away_team:
                    self.logger.debug(
                        "Could not resolve teams for ESPN event %s (%s vs %s)",
                        espn_event_id, away_abbr, home_abbr,
                    )
                    result["errors"] += 1
                    continue

                # Scores
                home_score_raw = home_comp.get("score", None)
                away_score_raw = away_comp.get("score", None)
                try:
                    home_score = int(home_score_raw) if home_score_raw not in (None, "") else None
                    away_score = int(away_score_raw) if away_score_raw not in (None, "") else None
                except (ValueError, TypeError):
                    home_score = away_score = None

                # Status
                state = event.get("status", {}).get("type", {}).get("state", "pre")
                espn_desc = event.get("status", {}).get("type", {}).get("description", "")
                if state == "post":
                    status = GameStatus.FINAL
                elif state == "in":
                    status = GameStatus.IN_PROGRESS
                elif "postponed" in espn_desc.lower():
                    status = GameStatus.POSTPONED
                elif "canceled" in espn_desc.lower() or "cancelled" in espn_desc.lower():
                    status = GameStatus.CANCELLED
                else:
                    status = GameStatus.SCHEDULED

                # Game time — convert from UTC to Eastern
                game_time = None
                event_date_raw = event.get("date", "")
                if event_date_raw:
                    try:
                        dt = datetime.datetime.fromisoformat(event_date_raw.replace("Z", "+00:00"))
                        from django.utils import timezone as django_tz
                        eastern = django_tz.get_current_timezone()
                        dt_eastern = dt.astimezone(eastern)
                        game_time = dt_eastern.time()
                    except (ValueError, TypeError):
                        pass

                # Venue
                venue = comp.get("venue", {}).get("fullName", "")

                # Find or create the Season
                year = target_date.year
                league = home_team.league
                season = None
                if league:
                    season, _ = Season.objects.get_or_create(
                        sport=self.sport,
                        league=league,
                        year=year,
                        defaults={"label": str(year), "is_current": True},
                    )

                # Try to find existing game by espn_id, then external_id, then team+date
                game_obj = Game.objects.filter(sport=self.sport, espn_id=espn_event_id).first()
                if game_obj is None:
                    game_obj = Game.objects.filter(
                        sport=self.sport,
                        game_date=target_date,
                        home_team=home_team,
                        away_team=away_team,
                    ).first()

                if game_obj is None:
                    # CREATE the game
                    game_obj = Game.objects.create(
                        sport=self.sport,
                        external_id=f"ESPN:{espn_event_id}",
                        espn_id=espn_event_id,
                        season=season,
                        home_team=home_team,
                        away_team=away_team,
                        game_date=target_date,
                        game_time=game_time,
                        status=status,
                        home_score=home_score,
                        away_score=away_score,
                        venue=venue,
                    )
                    result["created"] += 1
                else:
                    # UPDATE existing game
                    update_kwargs: dict = {
                        "status": status,
                        "espn_id": espn_event_id,
                    }
                    if home_score is not None:
                        update_kwargs["home_score"] = home_score
                    if away_score is not None:
                        update_kwargs["away_score"] = away_score
                    if game_time:
                        update_kwargs["game_time"] = game_time
                    if venue and not game_obj.venue:
                        update_kwargs["venue"] = venue

                    for k, v in update_kwargs.items():
                        setattr(game_obj, k, v)
                    game_obj.save(update_fields=list(update_kwargs.keys()) + ["updated_at"])
                    result["updated"] += 1

            except Exception as exc:
                self.logger.error(
                    "Error processing ESPN event %s: %s", event.get("id"), exc
                )
                result["errors"] += 1

        self._log_result("ingest_espn_scoreboard", result)
        return result

    @staticmethod
    def _extract_espn_injury_teams(data) -> list[dict]:
        """Normalise ESPN injury API responses.

        The ESPN API wraps injuries in ``{"injuries": [...]}`` where each entry
        has ``id``, ``displayName``, ``injuries`` at the top level — no nested
        ``team`` dict.  Older formats returned a bare list of team dicts with a
        nested ``team`` key.  This helper handles both.
        """
        if isinstance(data, dict):
            teams = data.get("injuries", data.get("teams", []))
        elif isinstance(data, list):
            teams = data
        else:
            return []

        normalised: list[dict] = []
        for entry in teams:
            if not isinstance(entry, dict):
                continue
            # New format: id/displayName at top level
            if "team" not in entry and "id" in entry:
                entry = {
                    "team": {"id": entry.get("id", ""), "displayName": entry.get("displayName", "")},
                    "injuries": entry.get("injuries", []),
                }
            normalised.append(entry)
        return normalised
