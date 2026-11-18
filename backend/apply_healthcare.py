"""One-off Greenhouse applications requested from findjobsin.healthcare."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from app.apply.greenhouse import (
    ApplyError,
    GreenhouseApplicator,
    UnmappedQuestion,
)
from app.apply.otp import fetch_code
from app.apply.profile import ProfileError, load_profile
from app.config import settings

ROOT = Path(__file__).resolve().parent.parent
LEDGER = ROOT / "data" / "applications.json"

JOBS = [
    {
        "key": "suki:8026661003",
        "source": "suki",
        "company": "Suki",
        "title": "Data Engineer",
        "location": "Bengaluru",
        "apply_url": "https://job-boards.greenhouse.io/suki/jobs/8026661003",
    },
    {
        "key": "zocdoc:8331078",
        "source": "zocdoc",
        "company": "Zocdoc",
        "title": "Senior Software Engineer",
        "location": "Pune",
        "apply_url": "https://job-boards.greenhouse.io/zocdoc/jobs/8331078",
    },
    {
        "key": "zocdoc:8331079",
        "source": "zocdoc",
        "company": "Zocdoc",
        "title": "Senior Integration Health Engineer",
        "location": "Pune",
        "apply_url": "https://job-boards.greenhouse.io/zocdoc/jobs/8331079",
    },
    {
        "key": "truveta:6240382004",
        "source": "truveta",
        "company": "Truveta",
        "title": "Software Engineer - Data Quality",
        "location": "Hyderabad",
        "apply_url": "https://job-boards.greenhouse.io/truveta/jobs/6240382004",
    },
]


def build_otp_provider() -> Callable[[datetime], str | None] | None:
    if not (settings.apply_imap_host and settings.apply_imap_user and settings.apply_imap_password):
        return None

    def fetch(since: datetime) -> str | None:
        return fetch_code(
            since,
            host=settings.apply_imap_host,
            user=settings.apply_imap_user,
            password=settings.apply_imap_password,
            folder=settings.apply_imap_folder,
            timeout=settings.apply_otp_timeout_seconds,
        )

    return fetch


def record(job: dict, outcome) -> None:
    ledger = json.loads(LEDGER.read_text(encoding="utf-8")) if LEDGER.exists() else {"applications": {}}
    applications = ledger.setdefault("applications", {})
    existing = applications.get(job["key"], {})
    entry = {
        "apply_url": job["apply_url"],
        "attempted_at": datetime.now(UTC).isoformat(),
        "attempts": int(existing.get("attempts") or 0) + 1,
        "company": job["company"],
        "confirmation_url": outcome.confirmation_url,
        "error": outcome.error,
        "external_id": job["key"].split(":")[1],
        "job_id": None,
        "screenshot": outcome.screenshot,
        "source": job["source"],
        "status": outcome.status,
        "title": job["title"],
        "origin": "findjobsin.healthcare",
    }
    if existing.get("status") == "submitted":
        return
    applications[job["key"]] = entry
    LEDGER.write_text(json.dumps(ledger, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="findjobsin.healthcare batch apply")
    parser.add_argument("--live", action="store_true", help="Submit applications")
    parser.add_argument("--only", default="", help="Comma-separated source keys to run")
    args = parser.parse_args()

    jobs = [j for j in JOBS if not args.only or j["key"] in {s.strip() for s in args.only.split(",")}]
    try:
        profile = load_profile()
        profile.validate(live=args.live)
    except ProfileError as exc:
        print(f"Profile error: {exc}", file=sys.stderr)
        return 2

    otp_provider = build_otp_provider() if args.live else None
    applicator = GreenhouseApplicator(headless=True)
    failures = 0
    for job in jobs:
        print("=" * 70)
        print(f"{job['company']} — {job['title']} ({job['location']})")
        try:
            outcome = applicator.apply(
                apply_url=job["apply_url"],
                company=job["company"],
                title=job["title"],
                profile=profile,
                dry_run=not args.live,
                otp_provider=otp_provider,
            )
        except UnmappedQuestion as exc:
            print(f"  NEEDS MANUAL: {exc}")
            failures += 1
            continue
        except ApplyError as exc:
            print(f"  ERROR: {exc}")
            failures += 1
            continue
        print(f"  status: {outcome.status}")
        if outcome.error:
            print(f"  error: {outcome.error}")
        if outcome.confirmation_url:
            print(f"  confirmation: {outcome.confirmation_url}")
        print(f"  screenshot: {outcome.screenshot}")
        if args.live:
            record(job, outcome)
        if outcome.status not in {"submitted", "dry_run"}:
            failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
