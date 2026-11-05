"""Open a headed Chrome profile so you can log in once.

Usage:
    python login_ats.py --source jpmorgan --url https://jpmc.fa.oraclecloud.com/...
    python login_ats.py --source pwc_india --url https://pwc.wd3.myworkdayjobs.com/...

The profile lives under data/sessions/chrome-<source>, so cookies and
localStorage persist even if you close the window. Log in (including SSO/2FA);
the autofill runner reuses the same profile.
"""

from __future__ import annotations

import argparse
import contextlib

from playwright.sync_api import sync_playwright

from app.apply.session import profile_dir


def main() -> int:
    parser = argparse.ArgumentParser(description="Create an authenticated ATS profile")
    parser.add_argument("--source", required=True, help="Source key, e.g. jpmorgan")
    parser.add_argument("--url", required=True, help="Login or job page to open")
    parser.add_argument(
        "--wait",
        type=int,
        default=900,
        help="Seconds before the browser closes automatically (default 900)",
    )
    parser.add_argument("--headless", action="store_true")
    args = parser.parse_args()

    directory = profile_dir(args.source)
    directory.mkdir(parents=True, exist_ok=True)
    print(f"Chrome profile: {directory}")
    print(f"Log in within {args.wait} seconds; closing the window also saves the session.")

    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(directory),
            channel="chrome",
            headless=args.headless,
            viewport={"width": 1366, "height": 900},
        )
        page = context.pages[0] if context.pages else context.new_page()
        try:
            page.goto(args.url, wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_timeout(args.wait * 1000)
        except Exception as exc:  # noqa: BLE001 - closing the window is expected
            print(f"Login window closed: {type(exc).__name__}")
        finally:
            with contextlib.suppress(Exception):
                context.close()
    print(f"Profile saved: {directory}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
