from __future__ import annotations

import logging
import signal
import sys
import time
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from threading import Event

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.config import settings
from app.database import init_db
from app.logging_config import configure_logging
from app.runner import ScrapeOptions, run_scrape

logger = logging.getLogger(__name__)
stop_event = Event()


def run_scrape_job() -> None:
    try:
        run_scrape(ScrapeOptions(trigger="scheduler"))
    except Exception:  # noqa: BLE001 - scheduler must survive a failed run
        logger.exception("Scheduled scrape run crashed; scheduler will keep running")

    if settings.fit_score_enabled and settings.fit_score_after_scrape:
        try:
            from app.fit.scoring import run_fit_scoring

            summary = run_fit_scoring(limit=settings.fit_max_jobs)
            logger.info(
                "Post-scrape fit scoring: %d heuristic, %d LLM, %.1fs",
                summary.heuristic_saved,
                summary.llm_scored,
                summary.duration_seconds,
            )
        except Exception:  # noqa: BLE001 - fit scoring must not stop the scheduler
            logger.exception("Post-scrape fit scoring failed; scheduler will keep running")


def _handle_signal(signum, _frame) -> None:  # type: ignore[no-untyped-def]
    logger.info("Received signal %s; shutting down scheduler", signum)
    stop_event.set()
    with suppress(Exception):
        scheduler.shutdown(wait=False)
    raise SystemExit(0)


scheduler = BackgroundScheduler(timezone="UTC")


def main() -> None:
    configure_logging()
    init_db()

    interval = IntervalTrigger(hours=settings.scrape_interval_hours, timezone="UTC")
    first_run = datetime.now(UTC) if settings.scrape_on_startup else None
    scheduler.add_job(
        run_scrape_job,
        trigger=interval,
        id="scrape-all-sources",
        name="Scrape all job sources",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
        next_run_time=first_run,
    )
    scheduler.start()
    logger.info(
        "Scheduler started: every %.1f hours, sources=%s, run_on_startup=%s",
        settings.scrape_interval_hours,
        settings.enabled_sources,
        settings.scrape_on_startup,
    )
    if first_run is None:
        logger.info("First scheduled run at %s", datetime.now(UTC) + timedelta(hours=settings.scrape_interval_hours))

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    try:
        # Long-lived worker process; APScheduler's background thread does the work.
        while True:
            time.sleep(3600)
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler stopping")
    finally:
        with suppress(Exception):
            scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001
        logger.exception("Scheduler crashed")
        sys.exit(1)
