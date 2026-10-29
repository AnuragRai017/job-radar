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

    score = subparsers.add_parser("score", help="Score active jobs against the resume")
    score.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum LLM-scored jobs (default: FIT_MAX_JOBS)",
    )
    score.add_argument(
        "--no-llm",
        action="store_true",
        help="Only compute deterministic heuristic scores",
    )
    score.add_argument(
        "--force",
        action="store_true",
        help="Rescore jobs even when the resume and LLM result are unchanged",
    )
    score.add_argument("--resume", default=None, help="Override RESUME_PATH")

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

    if args.command == "score":
        from app.fit.scoring import run_fit_scoring

        try:
            summary = run_fit_scoring(
                limit=args.limit,
                use_llm=not args.no_llm,
                force=args.force,
                resume_path=args.resume,
            )
        except (FileNotFoundError, ValueError) as exc:
            raise SystemExit(str(exc)) from exc
        print(
            f"Scored {summary.scanned} active jobs: "
            f"{summary.heuristic_saved} heuristic, {summary.llm_scored} LLM "
            f"({summary.llm_calls} calls, {summary.llm_failures} failures) "
            f"in {summary.duration_seconds:.1f}s"
        )
        if summary.top:
            print("\nTop matches:")
            for item in summary.top:
                print(
                    f"  {item.score:3d}  {item.company} | {item.title} | "
                    f"{item.location or 'Location not listed'} [{item.method}]"
                )
        return

    with session_scope() as db:
        total = db.execute(select(func.count()).select_from(Job)).scalar_one()
        active = db.execute(
            select(func.count()).select_from(Job).where(Job.is_active.is_(True))
        ).scalar_one()
        scored = db.execute(
            select(func.count(Job.fit_score)).where(Job.is_active.is_(True))
        ).scalar_one()
        best_fit = db.execute(
            select(func.max(Job.fit_score)).where(Job.is_active.is_(True))
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
    print(f"Fit scored: {scored} active jobs (best: {best_fit if best_fit is not None else '—'})")
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
