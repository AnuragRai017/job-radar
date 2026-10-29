from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

from app.config import settings
from app.database import session_scope
from app.models import ScrapeRun
from app.repository import (
    deactivate_missing,
    load_known_jobs,
    touch_seen,
    upsert_jobs,
)
from app.scrapers.models import ScrapeContext, ScrapeResult
from app.scrapers.registry import enabled_source_names, get_scraper
from app.utils import utcnow

logger = logging.getLogger(__name__)


@dataclass
class ScrapeOptions:
    sources: list[str] | None = None
    max_jobs: int | None = None
    max_pages: int | None = None
    trigger: str = "scheduler"
    notes: list[str] = field(default_factory=list)


def run_scrape(options: ScrapeOptions | None = None) -> int:
    """Run all enabled scrapers, isolate failures, and persist a run record."""
    options = options or ScrapeOptions()
    sources = options.sources or enabled_source_names()
    max_jobs = (
        options.max_jobs
        if options.max_jobs is not None
        else settings.scrape_max_jobs_per_source
    )
    max_pages = (
        options.max_pages
        if options.max_pages is not None
        else settings.scrape_max_pages_per_source
    )

    with session_scope() as db:
        run = ScrapeRun(trigger=options.trigger, status="running", started_at=utcnow(), sources={})
        db.add(run)
        db.flush()
        run_id = run.id

    logger.info(
        "Scrape run %d started: sources=%s max_jobs=%s max_pages=%s",
        run_id,
        sources,
        max_jobs or "unlimited",
        max_pages or "default",
    )

    summaries: dict[str, dict] = {}
    total_found = 0
    total_upserted = 0
    total_errors = 0

    for source in sources:
        started = time.monotonic()
        logger.info("Scrape run %d: starting %s", run_id, source)
        try:
            summary, result = _run_source(source, max_jobs=max_jobs, max_pages=max_pages)
        except Exception as exc:  # noqa: BLE001 - one source must not kill the run
            duration = round(time.monotonic() - started, 2)
            logger.exception("Scrape run %d: %s failed", run_id, source)
            summaries[source] = {
                "status": "failed",
                "found": 0,
                "upserted": 0,
                "details_fetched": 0,
                "errors": 1,
                "complete": False,
                "notes": [str(exc)],
                "duration_seconds": duration,
            }
            total_errors += 1
            continue

        duration = round(time.monotonic() - started, 2)
        summary["duration_seconds"] = duration
        summaries[source] = summary
        total_found += summary["found"]
        total_upserted += summary["upserted"]
        total_errors += summary["errors"]
        logger.info(
            "Scrape run %d: %s %s in %.1fs (found=%d, upserted=%d, errors=%d)",
            run_id,
            source,
            summary["status"],
            duration,
            summary["found"],
            summary["upserted"],
            summary["errors"],
        )

    if not summaries or all(item["status"] == "failed" for item in summaries.values()):
        overall_status = "failed"
    elif all(item["status"] == "success" for item in summaries.values()):
        overall_status = "success"
    else:
        overall_status = "partial"

    error_text = None
    failed = [name for name, item in summaries.items() if item["status"] == "failed"]
    if failed:
        error_text = f"Failed sources: {', '.join(failed)}"

    with session_scope() as db:
        run = db.get(ScrapeRun, run_id)
        if run is not None:
            run.status = overall_status
            run.finished_at = utcnow()
            run.total_found = total_found
            run.total_upserted = total_upserted
            run.total_errors = total_errors
            run.sources = summaries
            run.error = error_text

    logger.info(
        "Scrape run %d finished: status=%s found=%d upserted=%d errors=%d",
        run_id,
        overall_status,
        total_found,
        total_upserted,
        total_errors,
    )
    return run_id


def _run_source(source: str, *, max_jobs: int, max_pages: int) -> tuple[dict, ScrapeResult]:
    scraper = get_scraper(source)
    with session_scope() as db:
        known = load_known_jobs(db, source)
    logger.info("%s: %d known jobs in database", source, len(known))

    ctx = ScrapeContext(known=known, max_jobs=max_jobs, max_pages=max_pages)
    result = asyncio.run(scraper.scrape(ctx))

    if not result.seen_ids:
        result.complete = False
        result.note("No jobs discovered; existing records kept")

    with session_scope() as db:
        upserted = upsert_jobs(db, result.jobs)
        touch_seen(db, source, result.seen_ids)
        if result.complete and result.seen_ids:
            deactivate_missing(db, source, result.seen_ids)

    if result.complete and result.errors == 0:
        status = "success"
    elif result.seen_ids:
        status = "partial"
    else:
        status = "failed"

    summary = {
        "status": status,
        "found": len(result.seen_ids),
        "upserted": upserted,
        "details_fetched": result.details_fetched,
        "errors": result.errors,
        "complete": result.complete,
        "notes": result.notes,
    }
    return summary, result
