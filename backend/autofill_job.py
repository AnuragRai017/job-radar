"""Auto-fill an ATS application form using a saved authenticated session.

This never submits. It opens the job page, clicks Apply when present, fills the
fields it recognises, takes a screenshot, and reports the remaining required
fields for review.

Usage:
    python autofill_job.py --source jpmorgan --job-id 12345
    python autofill_job.py --source pwc_india --url https://pwc.wd3...
"""

from __future__ import annotations

import argparse
import contextlib
from datetime import UTC, datetime

from curl_cffi import requests as curl_requests
from playwright.sync_api import sync_playwright

from app.apply.autofill import autofill, summarize, unfilled_required
from app.apply.paths import SCREENSHOT_DIR
from app.apply.profile import load_profile
from app.apply.session import has_session, load_state, profile_dir

APPLY_LABELS = (
    "Apply now",
    "APPLY NOW",
    "Apply for this job",
    "Apply",
    "APPLY",
)


def click_apply(page) -> str:
    for label in APPLY_LABELS:
        for role in ("button", "link"):
            try:
                page.get_by_role(role, name=label, exact=False).first.click(timeout=2_500)
                return f"{role}:{label}"
            except Exception:  # noqa: BLE001
                continue
    return "not-found"


def fetch_job(api_base: str, job_id: int) -> dict:
    response = curl_requests.get(f"{api_base.rstrip('/')}/api/jobs/{job_id}", timeout=30)
    response.raise_for_status()
    payload = response.json()
    return payload if isinstance(payload, dict) else {}


def main() -> int:
    parser = argparse.ArgumentParser(description="Auto-fill an ATS application")
    parser.add_argument("--source", required=True, help="Session/source key")
    parser.add_argument("--job-id", type=int, default=None)
    parser.add_argument("--url", default=None, help="Apply URL (overrides --job-id)")
    parser.add_argument("--api-base", default="http://127.0.0.1:5200")
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--wait", type=int, default=4, help="Seconds to wait after Apply")
    args = parser.parse_args()

    job: dict = {}
    url = args.url
    if args.job_id is not None:
        job = fetch_job(args.api_base, args.job_id)
        url = url or str(job.get("apply_url") or "")
    if not url:
        print("Provide --url or a job with an apply_url.")
        return 2

    use_profile = has_session(args.source)
    state = None if use_profile else load_state(args.source)
    if not use_profile and state is None:
        print(
            f"No saved session for {args.source}. Run:\n"
            f"  python login_ats.py --source {args.source} --url <login-or-job-url>"
        )
        return 2

    profile = load_profile()
    profile.validate(live=False)

    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    screenshot = SCREENSHOT_DIR / f"autofill-{args.source}-{stamp}.png"

    with sync_playwright() as playwright:
        browser = None
        if use_profile:
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir(args.source)),
                channel="chrome",
                headless=not args.headed,
                viewport={"width": 1366, "height": 900},
            )
            page = context.pages[0] if context.pages else context.new_page()
        else:
            browser = playwright.chromium.launch(
                channel="chrome", headless=not args.headed
            )
            context = browser.new_context(
                storage_state=state, viewport={"width": 1366, "height": 900}
            )
            page = context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=90_000)
            page.wait_for_timeout(2_000)
            clicked = click_apply(page)
            page.wait_for_timeout(args.wait * 1000)
            results = autofill(page, profile)
            remaining = unfilled_required(page)
            page.screenshot(path=str(screenshot), full_page=True)
            print(f"URL: {page.url}")
            print(f"Apply click: {clicked}")
            print(f"Filled: {summarize(results)}")
            if remaining:
                print("Still required:")
                for label in remaining:
                    print(f"  - {label}")
            print(f"Screenshot: {screenshot}")
            print("Not submitted. Review the page before submitting.")
        except Exception as exc:  # noqa: BLE001
            with contextlib.suppress(Exception):
                page.screenshot(path=str(screenshot), full_page=True)
            print(f"Autofill failed: {type(exc).__name__}: {str(exc)[:300]}")
            return 1
        finally:
            context.close()
            if browser is not None:
                browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
