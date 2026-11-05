"""Open a headed Chrome window so you can log in once, then save the session.

Usage:
    python login_ats.py --source jpmorgan --url https://jpmc.fa.oraclecloud.com/...
    python login_ats.py --source pwc_india --url https://pwc.wd3.myworkdayjobs.com/...

Log in (including SSO/2FA) during the wait. The script saves cookies and
localStorage to data/sessions/<source>.json for the autofill runner.
"""

from __future__ import annotations

import argparse

from playwright.sync_api import sync_playwright

from app.apply.session import save_state, session_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Save an authenticated ATS session")
    parser.add_argument("--source", required=True, help="Source key, e.g. jpmorgan")
    parser.add_argument("--url", required=True, help="Login or job page to open")
    parser.add_argument(
        "--wait",
        type=int,
        default=300,
        help="Seconds to keep the browser open for login (default 300)",
    )
    parser.add_argument("--headless", action="store_true")
    args = parser.parse_args()

    print(f"Session target: {session_path(args.source)}")
    print(f"A browser window will open. Log in within {args.wait} seconds.")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            channel="chrome", headless=args.headless
        )
        context = browser.new_context(viewport={"width": 1366, "height": 900})
        page = context.new_page()
        try:
            page.goto(args.url, wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_timeout(args.wait * 1000)
            path = save_state(context, args.source)
            print(f"Saved session: {path}")
        finally:
            context.close()
            browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
