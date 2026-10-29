from __future__ import annotations

import re
from datetime import UTC, date, datetime
from urllib.parse import urlparse


def utcnow() -> datetime:
    return datetime.now(UTC)


def as_utc(value: datetime) -> datetime:
    """SQLite drops tzinfo; treat stored values as UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def iso_utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    return as_utc(value).isoformat().replace("+00:00", "Z")


def parse_datetime(value: object) -> datetime | None:
    """Parse common source date formats, returning an aware UTC datetime."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return as_utc(value)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=UTC)

    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    # Oracle sometimes returns a date without time.
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"):
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        else:
            return None
    return as_utc(parsed)


def parse_date(value: object) -> date | None:
    parsed = parse_datetime(value)
    if parsed is not None:
        return parsed.date()
    if isinstance(value, date):
        return value
    return None


def epoch_to_date(seconds: object) -> date | None:
    """Google's AF_initDataCallback timestamps are [seconds, nanoseconds]."""
    if isinstance(seconds, (list, tuple)) and seconds:
        seconds = seconds[0]
    try:
        value = int(float(seconds))
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    try:
        return datetime.fromtimestamp(value, tz=UTC).date()
    except (OverflowError, OSError, ValueError):
        return None


def clean_text(value: object, *, max_length: int | None = None) -> str:
    if value is None:
        return ""
    text = re.sub(r"\s+", " ", str(value)).strip()
    if max_length and len(text) > max_length:
        return text[: max_length - 1].rstrip() + "…"
    return text


def safe_http_url(value: object, fallback: str = "") -> str:
    """Only permit http(s) links in stored/displayed apply URLs."""
    text = str(value or "").strip()
    if not text:
        return fallback
    parsed = urlparse(text)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return fallback
    return text
