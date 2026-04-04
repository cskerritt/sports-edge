"""
Cron endpoint for triggering data ingestion via HTTP.

Protected by CRON_SECRET env var. Use with any external cron service
(cron-job.org, EasyCron, UptimeRobot, Railway cron, GitHub Actions, etc.)

Example:
    GET /cron/update/?token=your-secret-here
    GET /cron/update/?token=your-secret-here&full=1
"""

import logging
import threading

from django.conf import settings
from django.core.management import call_command
from django.http import JsonResponse
from django.views.decorators.http import require_GET

logger = logging.getLogger("cron")


@require_GET
def cron_update(request):
    """Trigger the morning_update pipeline via HTTP.

    Query params:
        token (required) — must match CRON_SECRET env var
        full  (optional) — if "1", runs full ingest (teams + schedules)
        sport (optional) — limit to one sport (NBA, NFL, NHL, MLB)
    """
    token = request.GET.get("token", "")
    expected = getattr(settings, "CRON_SECRET", "")

    if not expected or token != expected:
        return JsonResponse({"error": "unauthorized"}, status=401)

    full_ingest = request.GET.get("full", "") == "1"
    sport = request.GET.get("sport", "")

    # Run in a background thread so the HTTP response returns immediately.
    # The web process stays alive while the thread runs.
    def _run():
        try:
            args = []
            if full_ingest:
                args.append("--full-ingest")
            if sport:
                args.extend(["--sport", sport.upper()])
            logger.info("Cron update started: args=%s", args)
            call_command("morning_update", *args)
            logger.info("Cron update completed successfully")
        except Exception:
            logger.exception("Cron update failed")

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()

    return JsonResponse({
        "status": "started",
        "full_ingest": full_ingest,
        "sport": sport or "all",
    })
