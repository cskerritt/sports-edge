import logging
from datetime import date


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
        """Return today's date (thin wrapper for easier testing)."""
        return date.today()
