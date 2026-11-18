"""Apply to an Ashby posting using the saved RoleRadar profile.

Fills every field, clicks Submit, then waits for a human to solve the
reCAPTCHA challenge in the headed browser window (never automated).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(r"C:\Users\anura\OneDrive\Documents\Default Project")
PROFILE_PATH = ROOT / "data" / "applicant.json"
LEDGER_PATH = ROOT / "data" / "applications.json"
OUT_DIR = ROOT / "data" / "applications"


def answer_yes_no(page, question_text: str, answer: str) -> str:
    locator = page.locator(
        f"xpath=//button[normalize-space(text())='{answer}']"
        f"[ancestor::div[1][contains(normalize-space(.), '{question_text}')]]"
    )
    if locator.count() == 0:
        locator = page.locator(
            f"xpath=//*[contains(normalize-space(text()), '{question_text}')]"
            f"[following::button[normalize-space(text())='{answer}'][1]]"
        )
    locator.first.click(timeout=8_000)
    return f"{question_text[:40]} -> {answer}"


def record(key: str, company: str, title: str, url: str, screenshot: str) -> None:
    ledger = (
        json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
        if LEDGER_PATH.exists()
        else {"applications": {}}
    )
    applications = ledger.setdefault("applications", {})
    if applications.get(key, {}).get("status") == "submitted":
        return
    applications[key] = {
        "apply_url": url,
        "attempted_at": datetime.now(UTC).isoformat(),
        "attempts": 1,
        "company": company,
        "confirmation_url": "",
        "error": "",
        "external_id": key.split(":")[-1],
        "job_id": None,
        "screenshot": screenshot,
        "source": "ashby",
        "status": "submitted",
        "title": title,
        "origin": "findjobsin.healthcare",
    }
    LEDGER_PATH.write_text(json.dumps(ledger, indent=2), encoding="utf-8")


def apply(
    url: str,
    resume: Path,
    company: str,
    title: str,
    captcha_wait: int,
    office_answer: str = "No",
) -> None:
    profile = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    posts: list[str] = []
    posted_file = OUT_DIR / f"ashby-{stamp}-posted.json"
    net_file = OUT_DIR / f"ashby-{stamp}-net.log"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, channel="chrome")
        page = browser.new_page(viewport={"width": 1366, "height": 1400})

        def on_response(response):
            try:
                if response.request.method == "POST" and "ashbyhq.com" in response.url:
                    with net_file.open("a", encoding="utf-8") as fh:
                        fh.write(f"{response.status} {response.url}\n")
            except Exception:  # noqa: BLE001 - request race
                pass
            if "postingApplication" in response.url:
                entry = f"{response.status} {response.url}"
                posts.append(entry)
                # Persist immediately: the window may close right after submit.
                posted_file.write_text(
                    json.dumps({"status": response.status, "url": response.url}),
                    encoding="utf-8",
                )

        page.on("response", on_response)
        page.goto(url, wait_until="networkidle", timeout=60_000)
        page.get_by_text("Apply for this Job", exact=False).first.click(timeout=10_000)
        page.wait_for_timeout(1_500)

        page.fill("#_systemfield_name", f"{profile['first_name']} {profile['last_name']}")
        page.fill("#_systemfield_email", profile["email"])
        page.set_input_files("#_systemfield_resume", str(resume))
        page.wait_for_timeout(2_500)
        answer_yes_no(page, "require sponsorship to work in the US", "Yes")
        page.fill("input[type=url]", profile["linkedin"])
        answer_yes_no(page, "comfortable coming into the", office_answer)
        page.get_by_label("Other Job Boards", exact=False).check(timeout=4_000)
        # Voluntary EEOC: reproduce the applicant's own prior selections.
        with contextlib.suppress(Exception):
            page.get_by_label("Male", exact=True).check(timeout=3_000)
        with contextlib.suppress(Exception):
            page.get_by_label("Decline to self-identify", exact=True).last.check(
                timeout=3_000
            )
        print("Filled. Clicking Submit — solve the reCAPTCHA in the window.", flush=True)

        page.get_by_role("button", name="Submit Application").click(timeout=10_000)

        deadline = time.monotonic() + captcha_wait
        success = False
        closed = False
        last_token = ""
        submit_clicks = 0

        def token_value() -> str:
            try:
                return page.eval_on_selector(
                    "textarea.g-recaptcha-response", "el => el.value || ''"
                )
            except Exception:  # noqa: BLE001 - mid-navigation
                return ""

        try:
            while time.monotonic() < deadline:
                body = page.inner_text("body").lower()
                if any(
                    marker in body
                    for marker in (
                        "application received",
                        "thanks for applying",
                        "thank you for applying",
                        "your application was",
                        "submitted successfully",
                    )
                ) or any(post.startswith("2") for post in posts):
                    success = True
                    break
                token = token_value()
                if token and token != last_token and submit_clicks < 5:
                    submit = page.get_by_role("button", name="Submit Application")
                    if submit.count() and submit.first.is_visible():
                        submit.first.click(timeout=5_000)
                        last_token = token
                        submit_clicks += 1
                        print(
                            f"captcha solved - clicked Submit ({submit_clicks})",
                            flush=True,
                        )
                page.wait_for_timeout(2_000)
        except Exception as exc:  # noqa: BLE001 - window closed by the user
            print("window closed:", type(exc).__name__, flush=True)
            closed = True

        if net_file.exists():
            print("network:", net_file.read_text(encoding="utf-8"), flush=True)

        if posted_file.exists():
            payload = json.loads(posted_file.read_text(encoding="utf-8"))
            if str(payload.get("status", "")).startswith("2"):
                success = True

        shot = OUT_DIR / f"ashby-{stamp}-final.png"
        if not closed:
            page.screenshot(path=str(shot), full_page=True)
        print("POSTS:", posts)
        print("SUCCESS:", success)
        print("screenshot:", shot)
        if success:
            record(
                f"commure:{url.rstrip('/').split('/')[-1]}",
                company,
                title,
                url,
                str(shot),
            )
        with contextlib.suppress(Exception):
            browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("url")
    parser.add_argument("--company", default="Commure")
    parser.add_argument("--title", default="Software Engineer, Claims & AI Billing")
    parser.add_argument("--captcha-wait", type=int, default=420)
    parser.add_argument(
        "--office-answer", choices=("Yes", "No"), default="No", help="Mountain View 3 days/week"
    )
    parser.add_argument(
        "--resume",
        default=r"C:\Users\anura\Downloads\Anurag Rai.pdf",
    )
    args = parser.parse_args()
    apply(
        args.url,
        Path(args.resume),
        args.company,
        args.title,
        args.captcha_wait,
        args.office_answer,
    )
