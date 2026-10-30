from __future__ import annotations

from datetime import date

from sqlalchemy import select

from app.models import Job
from app.repository import (
    deactivate_missing,
    load_known_jobs,
    touch_seen,
    upsert_jobs,
)
from app.scrapers.models import JobRecord


def _record(
    *,
    source: str = "deloitte",
    external_id: str = "1",
    title: str = "Cyber Consultant",
    location: str = "New York, NY",
    description: str = "<p>Help clients with <strong>security</strong>.</p>",
    posted: date | None = date(2025, 1, 1),
    job_code: str = "",
) -> JobRecord:
    return JobRecord(
        source=source,
        company="Deloitte" if source == "deloitte" else "Google",
        external_id=external_id,
        job_code=job_code,
        title=title,
        location=location,
        description_html=description,
        apply_url=f"https://example.com/{external_id}",
        posted_date=posted,
    )


def test_upsert_sanitizes_and_preserves_first_seen(db) -> None:
    assert upsert_jobs(db, [_record(description="<p>Hello<script>bad()</script></p>")]) == 1

    job = db.execute(select(Job)).scalar_one()
    assert job.title == "Cyber Consultant"
    assert job.description_html == "<p>Hello</p>"
    assert job.description_text == "Hello"
    assert job.first_seen_at is not None
    first_seen = job.first_seen_at

    assert upsert_jobs(db, [_record(title="Senior Cyber Consultant")]) == 1
    db.expire_all()
    job = db.execute(select(Job)).scalar_one()
    assert job.title == "Senior Cyber Consultant"
    assert job.first_seen_at == first_seen
    assert db.execute(select(Job)).scalars().all().__len__() == 1


def test_upsert_deduplicates_within_a_batch(db) -> None:
    records = [_record(title="First"), _record(title="Second")]
    assert upsert_jobs(db, records) == 1

    job = db.execute(select(Job)).scalar_one()
    assert job.title == "Second"


def test_upsert_keeps_sources_independent(db) -> None:
    upsert_jobs(db, [_record(source="deloitte", external_id="shared")])
    upsert_jobs(db, [_record(source="google", external_id="shared")])
    assert len(db.execute(select(Job)).scalars().all()) == 2


def test_upsert_empty_batch(db) -> None:
    assert upsert_jobs(db, []) == 0


def test_upsert_stores_native_job_code_and_falls_back_to_external_id(db) -> None:
    upsert_jobs(db, [_record(external_id="abc", job_code="")])
    job = db.execute(select(Job)).scalar_one()
    assert job.job_code == "abc"

    upsert_jobs(db, [_record(external_id="abc", job_code="REQ-9001")])
    db.expire_all()
    job = db.execute(select(Job)).scalar_one()
    assert job.job_code == "REQ-9001"

    known = load_known_jobs(db, "deloitte")
    assert known["abc"].job_code == "REQ-9001"


def test_load_known_jobs_reports_descriptions(db) -> None:
    upsert_jobs(db, [_record(external_id="1"), _record(external_id="2", description="")])

    known = load_known_jobs(db, "deloitte")
    assert set(known) == {"1", "2"}
    assert known["1"].has_description is True
    assert known["1"].title == "Cyber Consultant"
    assert known["2"].has_description is False


def test_touch_seen_marks_jobs_active(db) -> None:
    upsert_jobs(db, [_record(external_id="1"), _record(external_id="2")])
    deactivate_missing(db, "deloitte", set())

    touch_seen(db, "deloitte", {"2"})
    db.expire_all()
    jobs = {
        job.external_id: job for job in db.execute(select(Job)).scalars().all()
    }
    assert jobs["1"].is_active is False
    assert jobs["2"].is_active is True
    assert jobs["2"].last_seen_at is not None


def test_deactivate_missing_only_affects_given_source(db) -> None:
    upsert_jobs(
        db,
        [
            _record(source="deloitte", external_id="1"),
            _record(source="deloitte", external_id="2"),
            _record(source="google", external_id="3"),
        ],
    )

    assert deactivate_missing(db, "deloitte", {"1"}) == 1
    db.expire_all()
    states = {
        (job.source, job.external_id): job.is_active
        for job in db.execute(select(Job)).scalars().all()
    }
    assert states[("deloitte", "1")] is True
    assert states[("deloitte", "2")] is False
    assert states[("google", "3")] is True
    assert deactivate_missing(db, "deloitte", {"1"}) == 0
