"""Run the Greenhouse auto-apply queue.

Examples:
    # Fill the form and take a screenshot, but do not submit
    python run_apply.py --limit 3

    # Submit for real (requires data/applicant.json with confirmed=true)
    python run_apply.py --live --limit 10
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from datetime import UTC, datetime

from app.apply.greenhouse import ApplyError, GreenhouseApplicator, UnmappedQuestion
from app.apply.otp import fetch_code
from app.apply.profile import ProfileError, load_profile
from app.apply.queue import (
    application_key,
    fetch_jobs,
    load_tracking,
    save_tracking,
    select_queue,
    submitted_today,
)
from app.config import settings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Greenhouse auto-apply runner")
    parser.add_argument("--live", action="store_true", help="Submit applications (default: dry run)")
    parser.add_argument("--limit", type=int, default=10, help="Max applications today")
    parser.add_argument("--min-fit", type=int, default=51, help="Minimum fit score")
    parser.add_argument(
        "--sources",
        default="medallion,hubspot",
        help="Comma-separated Greenhouse sources",
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Prompt for the emailed verification code when IMAP is not configured",
    )
    parser.add_argument("--api-base", default="http://127.0.0.1:5200")
    parser.add_argument("--profile", default=None, help="Path to applicant.json")
    parser.add_argument("--tracking", default=None, help="Path to applications.json")
    parser.add_argument("--headless", action="store_true", default=True)
    parser.add_argument("--headed", dest="headless", action="store_false")
    return parser.parse_args()


def build_otp_provider(args: argparse.Namespace) -> Callable[[datetime], str | None] | None:
    if (
        settings.apply_imap_host
        and settings.apply_imap_user
        and settings.apply_imap_password
    ):
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
    if args.interactive:
        def prompt(since: datetime) -> str | None:
            del since
            return input("Enter the 8-character code from your email: ").strip() or None

        return prompt
    return None


def main() -> int:
    args = parse_args()
    sources = [item.strip().lower() for item in args.sources.split(",") if item.strip()]

    try:
        profile = load_profile(args.profile)
        profile.validate(live=args.live)
    except ProfileError as exc:
        print(f"Profile error: {exc}", file=sys.stderr)
        return 2

    tracking = load_tracking(args.tracking)
    already_today = submitted_today(tracking)
    remaining = max(0, args.limit - already_today)
    if remaining == 0:
        print(f"Daily limit reached: {already_today}/{args.limit} submitted today.")
        return 0

    jobs = fetch_jobs(
        api_base=args.api_base,
        sources=sources,
        min_fit=args.min_fit,
    )
    queue = select_queue(
        jobs,
        tracking,
        daily_limit=args.limit,
        remaining_today=remaining,
    )
    print(
        f"Mode: {'LIVE' if args.live else 'DRY RUN'} | "
        f"sources={sources} | min_fit={args.min_fit} | "
        f"today={already_today}/{args.limit} | candidates={len(queue)}"
    )
    if not queue:
        print("No eligible Greenhouse applications found.")
        return 0

    applicator = GreenhouseApplicator(headless=args.headless)
    otp_provider = build_otp_provider(args)
    submitted = 0
    for index, job in enumerate(queue, start=1):
        key = application_key(job)
        entry = tracking.setdefault("applications", {}).setdefault(key, {})
        entry.update(
            {
                "job_id": job.get("id"),
                "source": job.get("source"),
                "external_id": job.get("external_id") or job.get("job_code"),
                "company": job.get("company"),
                "title": job.get("title"),
                "fit_score": job.get("fit_score"),
                "apply_url": job.get("apply_url"),
            }
        )
        entry["attempts"] = int(entry.get("attempts") or 0)

        print(f"[{index}/{len(queue)}] {job.get('company')} | {job.get('title')} | fit={job.get('fit_score')}")
        try:
            outcome = applicator.apply(
                apply_url=str(job.get("apply_url") or ""),
                company=str(job.get("company") or ""),
                title=str(job.get("title") or ""),
                profile=profile,
                dry_run=not args.live,
                otp_provider=otp_provider,
            )
        except UnmappedQuestion as exc:
            entry.update(
                {
                    "status": "needs_manual",
                    "error": str(exc),
                    "attempted_at": datetime.now(UTC).isoformat(),
                }
            )
            save_tracking(tracking, args.tracking)
            print(f"  needs manual review: {exc}")
            continue
        except ApplyError as exc:
            entry.update(
                {
                    "status": "failed",
                    "error": str(exc),
                    "attempts": entry["attempts"] + 1,
                    "attempted_at": datetime.now(UTC).isoformat(),
                }
            )
            save_tracking(tracking, args.tracking)
            print(f"  error: {exc}")
            continue

        entry.update(
            {
                "status": outcome.status,
                "confirmation_url": outcome.confirmation_url,
                "screenshot": outcome.screenshot,
                "error": outcome.error,
                "attempted_at": datetime.now(UTC).isoformat(),
            }
        )
        if outcome.status in {"failed", "needs_manual", "needs_code"}:
            entry["attempts"] = entry["attempts"] + 1
        save_tracking(tracking, args.tracking)

        if outcome.status == "submitted":
            submitted += 1
            print(f"  submitted: {outcome.confirmation_url}")
        elif outcome.status == "dry_run":
            print(f"  dry run ok; screenshot: {outcome.screenshot}")
        else:
            print(f"  {outcome.status}: {outcome.error or 'see screenshot'}")

    print(f"Done: {submitted} submitted, {len(queue) - submitted} not submitted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
