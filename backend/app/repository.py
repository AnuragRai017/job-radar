from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.models import Job
from app.sanitize import prepare_description
from app.scrapers.models import JobRecord, KnownJob
from app.utils import utcnow

logger = logging.getLogger(__name__)

_UPSERT_COLUMNS = (
    "company",
    "job_code",
    "title",
    "location",
    "description_html",
    "description_text",
    "apply_url",
    "posted_date",
    "is_active",
    "last_seen_at",
    "scraped_at",
    "updated_at",
)


def load_known_jobs(db: Session, source: str) -> dict[str, KnownJob]:
    has_text = func.length(func.coalesce(Job.description_text, "")) > 0
    rows = db.execute(
        select(
            Job.external_id,
            Job.job_code,
            Job.title,
            Job.location,
            Job.posted_date,
            has_text.label("has_description"),
        ).where(Job.source == source)
    ).all()
    return {
        row.external_id: KnownJob(
            title=row.title or "",
            location=row.location or "",
            posted_date=row.posted_date,
            has_description=bool(row.has_description),
            job_code=row.job_code or "",
        )
        for row in rows
    }


def _row_for(record: JobRecord, now: datetime) -> dict:
    description_html, description_text = prepare_description(record.description_html)
    return {
        "source": record.source,
        "company": record.company,
        "external_id": record.external_id,
        "job_code": (record.job_code or record.external_id or "")[:160],
        "title": (record.title or "Untitled role")[:512],
        "location": (record.location or "")[:1024],
        "description_html": description_html,
        "description_text": description_text,
        "apply_url": (record.apply_url or "")[:2048],
        "posted_date": record.posted_date,
        "is_active": True,
        "first_seen_at": now,
        "last_seen_at": now,
        "scraped_at": now,
        "created_at": now,
        "updated_at": now,
    }


def upsert_jobs(db: Session, records: list[JobRecord]) -> int:
    if not records:
        return 0

    now = utcnow()
    deduped: dict[tuple[str, str], JobRecord] = {}
    for record in records:
        if record.external_id:
            deduped[(record.source, record.external_id)] = record
    rows = [_row_for(record, now) for record in deduped.values()]
    if not rows:
        return 0

    dialect = db.bind.dialect.name if db.bind is not None else "sqlite"
    batch_size = 50 if dialect == "sqlite" else 100

    for start in range(0, len(rows), batch_size):
        batch = rows[start : start + batch_size]
        if dialect == "postgresql":
            statement = pg_insert(Job).values(batch)
        elif dialect == "sqlite":
            statement = sqlite_insert(Job).values(batch)
        else:  # pragma: no cover - fallback for unusual test databases
            _generic_upsert(db, batch)
            continue

        statement = statement.on_conflict_do_update(
            index_elements=["source", "external_id"],
            set_={column: getattr(statement.excluded, column) for column in _UPSERT_COLUMNS},
        )
        db.execute(statement)

    db.commit()
    return len(rows)


def _generic_upsert(db: Session, batch: list[dict]) -> None:  # pragma: no cover
    for row in batch:
        existing = db.execute(
            select(Job).where(
                Job.source == row["source"], Job.external_id == row["external_id"]
            )
        ).scalar_one_or_none()
        if existing is None:
            db.add(Job(**row))
        else:
            for key, value in row.items():
                if key not in {"first_seen_at", "created_at"}:
                    setattr(existing, key, value)


def touch_seen(db: Session, source: str, seen_ids: set[str], *, active: bool = True) -> None:
    if not seen_ids:
        return
    now = utcnow()
    values = sorted(seen_ids)
    for start in range(0, len(values), 500):
        chunk = values[start : start + 500]
        db.execute(
            update(Job)
            .where(Job.source == source, Job.external_id.in_(chunk))
            .values(last_seen_at=now, is_active=active)
        )
    db.commit()


def deactivate_missing(db: Session, source: str, seen_ids: set[str]) -> int:
    active_ids = db.execute(
        select(Job.external_id).where(Job.source == source, Job.is_active.is_(True))
    ).scalars()
    missing = [external_id for external_id in active_ids if external_id not in seen_ids]
    if not missing:
        return 0

    now = utcnow()
    for start in range(0, len(missing), 500):
        chunk = missing[start : start + 500]
        db.execute(
            update(Job)
            .where(Job.source == source, Job.external_id.in_(chunk))
            .values(is_active=False, updated_at=now)
        )
    db.commit()
    logger.info("Deactivated %d stale jobs for %s", len(missing), source)
    return len(missing)
