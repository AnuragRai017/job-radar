"""Fill and submit the Innovaccer (Workable) application with profile answers.

Live submissions are headed: the script fills everything, clicks Submit, and
waits for the human to tick Cloudflare Turnstile ("Verify you are human").
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(r"C:\Users\anura\OneDrive\Documents\Default Project")
PROFILE = json.loads((ROOT / "data" / "applicant.json").read_text(encoding="utf-8"))
RESUME = ROOT / "data" / "Anurag_Rai_Resume.pdf"
LEDGER = ROOT / "data" / "applications.json"
OUT_DIR = ROOT / "data" / "applications"

URL = "https://apply.workable.com/innovaccer-analytics/j/488D9AC184/apply/"
SUMMARY = (
    "Data & Platform Engineer building ETL/ELT pipelines, analytics platforms, and "
    "AI-driven automation on GCP. Owns an end-to-end Jira-to-BigQuery pipeline "
    "processing 50K+ tickets per month, 20+ BigQuery tables and views powering "
    "leadership dashboards, and LLM-based tooling (Vertex AI, MCP servers, support "
    "triage agent). Strong in Python, SQL, TypeScript, and REST API integrations."
)


def record(screenshot: str, status: str, error: str = "") -> None:
    ledger = (
        json.loads(LEDGER.read_text(encoding="utf-8"))
        if LEDGER.exists()
        else {"applications": {}}
    )
    applications = ledger.setdefault("applications", {})
    applications["innovaccer:488D9AC184"] = {
        "apply_url": URL,
        "attempted_at": datetime.now(UTC).isoformat(),
        "attempts": int(
            applications.get("innovaccer:488D9AC184", {}).get("attempts") or 0
        )
        + 1,
        "company": "Innovaccer",
        "confirmation_url": "",
        "error": error,
        "external_id": "488D9AC184",
        "job_id": None,
        "screenshot": screenshot,
        "source": "innovaccer",
        "status": status,
        "title": "Software Development Engineer-II Backend",
        "origin": "findjobsin.healthcare",
    }
    LEDGER.write_text(json.dumps(ledger, indent=2), encoding="utf-8")


def pick_notice_option(page) -> str:
    page.click("#input_CA_41169_input")
    page.wait_for_timeout(1_200)
    options = page.evaluate(
        """() => Array.from(document.querySelectorAll("[role=option], li"))
             .map(e => (e.innerText || "").trim())
             .filter(t => t && t.length < 40 && /day|month|notice|immediate/i.test(t))"""
    )
    print("NOTICE OPTIONS:", options)
    for choice in ("30 days", "1 month", "30", "Immediate"):
        for text in options:
            if choice.lower() in text.lower():
                page.get_by_text(text, exact=True).first.click(timeout=3_000)
                page.wait_for_timeout(500)
                return text
    raise RuntimeError(f"No notice option matching 30 days: {options}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--wait", type=int, default=420, help="Seconds to wait for Turnstile")
    args = parser.parse_args()

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=not args.headed, channel="chrome"
        )
        page = browser.new_page(viewport={"width": 1366, "height": 1200})
        page.goto(URL, wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_timeout(3_000)
        try:
            page.get_by_role("button", name="Accept all", exact=False).first.click(timeout=4_000)
            page.wait_for_timeout(800)
        except Exception as exc:  # noqa: BLE001
            print("cookies:", exc)

        page.fill("#firstname", PROFILE["first_name"])
        page.fill("#lastname", PROFILE["last_name"])
        page.fill("#email", PROFILE["email"])
        page.fill("#input_phone", "9651339863")
        page.fill("#address", "Bengaluru, Karnataka, India")
        page.fill("#CA_39286", PROFILE["current_ctc"])
        page.fill("#CA_39287", PROFILE["expected_ctc"])
        page.fill("#CA_39289", "Yes")
        page.fill("#summary", SUMMARY)
        notice = pick_notice_option(page)
        page.set_input_files("input[type=file]", str(RESUME))
        page.wait_for_timeout(2_500)

        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        filled = OUT_DIR / f"workable-innovaccer-{stamp}-filled.png"
        page.screenshot(path=str(filled), full_page=True)
        print("notice choice:", notice)
        print("filled screenshot:", filled)

        if not args.headed:
            print("Dry run - not submitting")
            browser.close()
            return

        page.get_by_role("button", name="Submit application").click(timeout=15_000)
        print("Submitted; tick 'Verify you are human' in the window.")

        deadline = time.monotonic() + args.wait
        success = False
        while time.monotonic() < deadline:
            body = page.inner_text("body").lower()
            if any(
                marker in body
                for marker in (
                    "application submitted",
                    "thank you for applying",
                    "your application has been",
                    "we received your application",
                )
            ):
                success = True
                break
            if "verify you are human" not in body and "/apply" not in page.url:
                success = True
                break
            page.wait_for_timeout(2_000)

        final = OUT_DIR / f"workable-innovaccer-{stamp}-final.png"
        page.screenshot(path=str(final), full_page=True)
        print("URL:", page.url)
        print("SUCCESS:", success)
        print("final screenshot:", final)
        record(str(final), "submitted" if success else "failed", "" if success else "Turnstile not completed")
        browser.close()


if __name__ == "__main__":
    main()
