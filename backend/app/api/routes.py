from __future__ import annotations

import math

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import Job, ScrapeRun
from app.sanitize import make_excerpt
from app.schemas import (
    CompanyStat,
    JobDetail,
    JobListResponse,
    JobSummary,
    RunSummary,
    StatsResponse,
)

router = APIRouter()


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _to_summary(job: Job) -> JobSummary:
    return JobSummary(
        id=job.id,
        source=job.source,
        company=job.company,
        external_id=job.external_id,
        title=job.title,
        location=job.location,
        apply_url=job.apply_url,
        posted_date=job.posted_date,
        excerpt=make_excerpt(job.description_text),
        is_active=job.is_active,
        scraped_at=job.scraped_at,
    )


def _to_detail(job: Job) -> JobDetail:
    return JobDetail(
        id=job.id,
        source=job.source,
        company=job.company,
        external_id=job.external_id,
        title=job.title,
        location=job.location,
        apply_url=job.apply_url,
        posted_date=job.posted_date,
        excerpt=make_excerpt(job.description_text),
        is_active=job.is_active,
        scraped_at=job.scraped_at,
        description_html=job.description_html,
        description_text=job.description_text,
        first_seen_at=job.first_seen_at,
        last_seen_at=job.last_seen_at,
    )


@router.get("/jobs", response_model=JobListResponse, summary="List jobs")
def list_jobs(
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(
        default=settings.page_size_default, ge=1, le=settings.page_size_max
    ),
    company: str | None = Query(None, max_length=80),
    location: str | None = Query(None, max_length=200),
    keyword: str | None = Query(None, max_length=200),
    include_inactive: bool = Query(False),
) -> JobListResponse:
    conditions = []
    if not include_inactive:
        conditions.append(Job.is_active.is_(True))
    if company and company.strip():
        conditions.append(func.lower(Job.company) == company.strip().lower())
    if location and location.strip():
        pattern = f"%{_escape_like(location.strip())}%"
        conditions.append(Job.location.ilike(pattern, escape="\\"))
    if keyword and keyword.strip():
        pattern = f"%{_escape_like(keyword.strip())}%"
        conditions.append(
            or_(
                Job.title.ilike(pattern, escape="\\"),
                Job.description_text.ilike(pattern, escape="\\"),
                Job.location.ilike(pattern, escape="\\"),
                Job.company.ilike(pattern, escape="\\"),
                Job.external_id.ilike(pattern, escape="\\"),
            )
        )

    total = db.execute(select(func.count()).select_from(Job).where(*conditions)).scalar_one()
    order = (
        case((Job.posted_date.is_(None), 1), else_=0),
        Job.posted_date.desc(),
        Job.id.desc(),
    )
    jobs = (
        db.execute(
            select(Job)
            .where(*conditions)
            .order_by(*order)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .scalars()
        .all()
    )

    pages = math.ceil(total / page_size) if total else 0
    return JobListResponse(
        items=[_to_summary(job) for job in jobs],
        total=total,
        page=page,
        page_size=page_size,
        pages=pages,
    )


@router.get("/jobs/{job_id}", response_model=JobDetail, summary="Get one job")
def get_job(job_id: int, db: Session = Depends(get_db)) -> JobDetail:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return _to_detail(job)


@router.get("/stats", response_model=StatsResponse, summary="Job and scrape statistics")
def get_stats(db: Session = Depends(get_db)) -> StatsResponse:
    rows = db.execute(
        select(
            Job.company,
            Job.source,
            func.count(Job.id).label("count"),
            func.sum(case((Job.is_active.is_(True), 1), else_=0)).label("active_count"),
        ).group_by(Job.company, Job.source)
    ).all()

    companies = [
        CompanyStat(
            company=row.company,
            source=row.source,
            count=int(row.count or 0),
            active_count=int(row.active_count or 0),
        )
        for row in rows
    ]
    companies.sort(key=lambda item: (-item.active_count, item.company))

    total_jobs = sum(item.count for item in companies)
    active_jobs = sum(item.active_count for item in companies)

    latest_run = db.execute(
        select(ScrapeRun).order_by(ScrapeRun.started_at.desc(), ScrapeRun.id.desc()).limit(1)
    ).scalar_one_or_none()
    latest_success = db.execute(
        select(ScrapeRun)
        .where(ScrapeRun.status == "success")
        .order_by(ScrapeRun.finished_at.desc(), ScrapeRun.id.desc())
        .limit(1)
    ).scalar_one_or_none()

    last_scrape_at = None
    if latest_run is not None:
        last_scrape_at = latest_run.finished_at or latest_run.started_at

    return StatsResponse(
        total_jobs=total_jobs,
        active_jobs=active_jobs,
        companies=companies,
        last_scrape_at=last_scrape_at,
        last_successful_scrape_at=(
            (latest_success.finished_at or latest_success.started_at)
            if latest_success is not None
            else None
        ),
        last_run=RunSummary.model_validate(latest_run) if latest_run is not None else None,
    )
