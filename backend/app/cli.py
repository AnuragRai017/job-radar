from __future__ import annotations

import argparse
import logging

from sqlalchemy import func, select

from app.config import settings
from app.database import init_db, session_scope
from app.logging_config import configure_logging
from app.models import Job, ScrapeRun
from app.runner import ScrapeOptions, run_scrape
from app.scrapers.registry import SCRAPER_CLASSES

logger = logging.getLogger(__name__)


def _parse_sources(value: str | None) -> list[str] | None:
    if not value or value.strip().lower() == "all":
        return None
    names = [name.strip().lower() for name in value.split(",") if name.strip()]
    unknown = [name for name in names if name not in SCRAPER_CLASSES]
    if unknown:
        raise SystemExit(
            f"Unknown source(s): {', '.join(unknown)}. "
            f"Choose from: {', '.join(SCRAPER_CLASSES)}"
        )
    return names


def main() -> None:
    parser = argparse.ArgumentParser(description="RoleRadar scraper utility")
    subparsers = parser.add_subparsers(dest="command", required=True)

    scrape = subparsers.add_parser("scrape", help="Run one scrape pass")
    scrape.add_argument(
        "--sources",
        default="all",
        help="Comma-separated sources, or 'all' (default)",
    )
    scrape.add_argument("--limit", type=int, default=None, help="Max jobs per source (0 = all)")
    scrape.add_argument("--pages", type=int, default=None, help="Max list pages per source")
    scrape.add_argument("--trigger", default="manual", help="Label stored on the scrape run")

    subparsers.add_parser("stats", help="Show database counts and the latest run")

    args = parser.parse_args()
    configure_logging()
    init_db()

    if args.command == "scrape":
        options = ScrapeOptions(
            sources=_parse_sources(args.sources),
            max_jobs=args.limit,
            max_pages=args.pages,
            trigger=args.trigger,
        )
        run_scrape(options)
        return

    with session_scope() as db:
        total = db.execute(select(func.count()).select_from(Job)).scalar_one()
        active = db.execute(
            select(func.count()).select_from(Job).where(Job.is_active.is_(True))
        ).scalar_one()
        rows = db.execute(
            select(Job.company, func.count(Job.id))
            .where(Job.is_active.is_(True))
            .group_by(Job.company)
            .order_by(func.count(Job.id).desc())
        ).all()
        latest = db.execute(
            select(ScrapeRun).order_by(ScrapeRun.started_at.desc(), ScrapeRun.id.desc()).limit(1)
        ).scalar_one_or_none()

    print(f"Database: {settings.database_url}")
    print(f"Jobs: {active} active / {total} total")
    for company, count in rows:
        print(f"  {company}: {count}")
    if latest is not None:
        print(
            f"Latest run: #{latest.id} {latest.status} "
            f"started={latest.started_at} finished={latest.finished_at}"
        )
    else:
        print("Latest run: none")


if __name__ == "__main__":
    main()
