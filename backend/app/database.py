from __future__ import annotations

import logging
import re
import time
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.models import Base

logger = logging.getLogger(__name__)


def _ensure_sqlite_directory(database_url: str) -> None:
    if not database_url.startswith("sqlite"):
        return
    database = make_url(database_url).database
    if not database or database == ":memory:":
        return
    Path(database).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)


def _build_engine(database_url: str) -> Engine:
    _ensure_sqlite_directory(database_url)
    kwargs: dict = {"pool_pre_ping": True, "echo": settings.db_echo, "future": True}
    if database_url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
    else:
        kwargs["pool_size"] = 5
        kwargs["max_overflow"] = 10

    engine = create_engine(database_url, **kwargs)

    if database_url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _set_sqlite_pragmas(dbapi_connection, _connection_record):  # type: ignore[no-untyped-def]
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.close()

    return engine


engine = _build_engine(settings.database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


_ADDITIVE_COLUMNS = {
    "job_code": "VARCHAR(160)",
    "fit_score": "INTEGER",
    "fit_verdict": "VARCHAR(16)",
    "fit_reasons": "JSON",
    "fit_gaps": "JSON",
    "fit_method": "VARCHAR(16)",
    "fit_model": "VARCHAR(128)",
    "fit_scored_at": "TIMESTAMP WITH TIME ZONE",
    "fit_resume_hash": "VARCHAR(64)",
}


def _ensure_additive_columns() -> None:
    """Add newer columns to an existing jobs table (additive migration)."""
    inspector = inspect(engine)
    if "jobs" not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns("jobs")}
    missing = {
        name: ddl for name, ddl in _ADDITIVE_COLUMNS.items() if name not in existing
    }
    if not missing:
        return

    dialect = engine.dialect.name
    with engine.begin() as connection:
        for name, ddl in missing.items():
            column_type = ddl
            if dialect == "sqlite":
                column_type = {
                    "JSON": "TEXT",
                    "TIMESTAMP WITH TIME ZONE": "DATETIME",
                }.get(ddl, ddl)
            connection.execute(text(f"ALTER TABLE jobs ADD COLUMN {name} {column_type}"))
        connection.execute(
            text("CREATE INDEX IF NOT EXISTS ix_jobs_fit_score ON jobs (fit_score)")
        )
        connection.execute(
            text("CREATE INDEX IF NOT EXISTS ix_jobs_job_code ON jobs (job_code)")
        )
    logger.info("Added columns to jobs: %s", ", ".join(sorted(missing)))


def _backfill_job_codes() -> None:
    """Fill native referral IDs where they can be derived without a detail fetch.

    Microsoft and HSBC use Eightfold's numeric display ID, which is only in the
    detail payload. Leave those empty so the next scrape refreshes the detail
    once; all other sources either already expose the native ID as external_id
    or carry it in the apply URL.
    """
    with engine.begin() as connection:
        rows = connection.execute(
            text(
                "SELECT id, source, external_id, apply_url FROM jobs "
                "WHERE job_code IS NULL OR job_code = ''"
            )
        ).fetchall()
        updated = 0
        for row in rows:
            source = (row.source or "").lower()
            external_id = (row.external_id or "").strip()
            apply_url = row.apply_url or ""
            code = ""
            if source == "amazon":
                match = re.search(r"/jobs/(\d+)", apply_url)
                code = match.group(1) if match else ""
            elif source.startswith("capgemini"):
                match = re.search(r"/job/[^/]+/(\d+)", apply_url)
                code = match.group(1) if match else ""
            elif source in {"microsoft", "hsbc"}:
                code = ""
            else:
                code = external_id
            if code:
                connection.execute(
                    text("UPDATE jobs SET job_code = :code WHERE id = :id"),
                    {"code": code[:160], "id": row.id},
                )
                updated += 1
        if updated:
            logger.info("Backfilled job_code for %d jobs", updated)


def init_db(max_attempts: int = 12, delay_seconds: float = 2.0) -> None:
    """Create tables, retrying while a fresh PostgreSQL container starts."""
    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            Base.metadata.create_all(bind=engine)
            _ensure_additive_columns()
            _backfill_job_codes()
            logger.info("Database ready (%s)", engine.url.render_as_string(hide_password=True))
            return
        except Exception as exc:  # noqa: BLE001 - surfaced after retries
            last_error = exc
            logger.warning(
                "Database not ready (attempt %d/%d): %s", attempt, max_attempts, exc
            )
            if attempt < max_attempts:
                time.sleep(delay_seconds)
    raise RuntimeError("Could not initialise the database") from last_error
