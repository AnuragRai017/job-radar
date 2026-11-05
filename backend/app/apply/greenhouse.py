from __future__ import annotations

import contextlib
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from curl_cffi import requests as curl_requests

from app.apply.paths import SCREENSHOT_DIR, resolve_data_path
from app.apply.profile import ApplicantProfile
from app.utils import utcnow

GREENHOUSE_URL_RE = re.compile(r"greenhouse\.io/([^/]+)/jobs/(\d+)")
QUESTIONS_API = "https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{job}?questions=true"


class ApplyError(RuntimeError):
    pass


class UnmappedQuestion(ApplyError):
    def __init__(self, label: str):
        self.label = label
        super().__init__(f"No answer configured for required question: {label}")


@dataclass(slots=True)
class ApplyOutcome:
    status: str
    confirmation_url: str = ""
    screenshot: str = ""
    error: str = ""


def parse_greenhouse_url(url: str) -> tuple[str, str]:
    match = GREENHOUSE_URL_RE.search(url or "")
    if not match:
        raise ApplyError(f"Not a Greenhouse job URL: {url}")
    return match.group(1), match.group(2)


def fetch_questions(board: str, job_id: str) -> list[dict]:
    response = curl_requests.get(
        QUESTIONS_API.format(board=board, job=job_id),
        headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0"},
        timeout=30,
    )
    if response.status_code != 200:
        raise ApplyError(f"Could not load application questions ({response.status_code})")
    payload = response.json()
    questions = payload.get("questions") if isinstance(payload, dict) else None
    return questions if isinstance(questions, list) else []


class GreenhouseApplicator:
    """Fill and submit a Greenhouse job-board application with Playwright.

    Uses the locally installed Chrome (``channel="chrome"``) so the invisible
    reCAPTCHA Enterprise check can run exactly as it does for a human browser.
    """

    def __init__(self, *, headless: bool = True, timeout_ms: int = 60_000):
        self.headless = headless
        self.timeout_ms = timeout_ms

    def apply(
        self,
        *,
        apply_url: str,
        company: str,
        title: str,
        profile: ApplicantProfile,
        dry_run: bool = True,
        otp_provider: Callable[[datetime], str | None] | None = None,
    ) -> ApplyOutcome:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise ApplyError(
                "Playwright is not installed. Run: pip install -r requirements-apply.txt"
            ) from exc

        board, job_id = parse_greenhouse_url(apply_url)
        questions = fetch_questions(board, job_id)
        screenshot = SCREENSHOT_DIR / f"{board}-{job_id}-{utcnow():%Y%m%d-%H%M%S}.png"
        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                channel="chrome", headless=self.headless
            )
            context = browser.new_context(viewport={"width": 1366, "height": 900})
            page = context.new_page()
            try:
                page.goto(apply_url, wait_until="domcontentloaded", timeout=self.timeout_ms)
                self._open_form(page)
                self._fill_core(page, profile)

                for question in questions:
                    self._fill_question(page, question, profile, company, title)

                page.wait_for_timeout(600)
                # Some Greenhouse boards re-render after the resume upload or a
                # custom-select change. Re-assert the core fields so React state
                # matches the DOM before validation runs.
                self._retype(page, "#first_name", profile.first_name)
                self._retype(page, "#last_name", profile.last_name)
                self._retype(page, "#email", profile.email)
                page.wait_for_timeout(500)
                page.screenshot(path=str(screenshot), full_page=True)

                if dry_run:
                    return ApplyOutcome(status="dry_run", screenshot=str(screenshot))

                submitted_at = utcnow()
                page.get_by_role("button", name="Submit application").first.click(
                    timeout=20_000
                )
                page.wait_for_timeout(2_500)

                if page.locator("#security-input-0").count():
                    code = otp_provider(submitted_at) if otp_provider else None
                    if not code:
                        page.screenshot(path=str(screenshot), full_page=True)
                        return ApplyOutcome(
                            status="needs_code",
                            screenshot=str(screenshot),
                            error=(
                                "Greenhouse sent a verification code. Configure "
                                "APPLY_IMAP_* in .env or run with --interactive."
                            ),
                        )
                    for index, character in enumerate(code[:8]):
                        page.fill(f"#security-input-{index}", character)
                    page.wait_for_timeout(800)
                    with contextlib.suppress(Exception):
                        page.get_by_role("button", name="Submit application").first.click(
                            timeout=5_000
                        )

                submitted = self._wait_for_confirmation(page)
                if not submitted:
                    error = self._visible_error(page)
                    page.screenshot(path=str(screenshot), full_page=True)
                    return ApplyOutcome(
                        status="failed",
                        screenshot=str(screenshot),
                        error=error or "Submission did not reach a confirmation page",
                    )

                page.screenshot(path=str(screenshot), full_page=True)
                return ApplyOutcome(
                    status="submitted",
                    confirmation_url=page.url,
                    screenshot=str(screenshot),
                )
            except UnmappedQuestion:
                raise
            except Exception as exc:  # noqa: BLE001 - record any browser failure
                with contextlib.suppress(Exception):
                    page.screenshot(path=str(screenshot), full_page=True)
                return ApplyOutcome(
                    status="failed",
                    screenshot=str(screenshot),
                    error=f"{type(exc).__name__}: {str(exc)[:400]}",
                )
            finally:
                context.close()
                browser.close()

    def _open_form(self, page) -> None:
        with contextlib.suppress(Exception):
            # Some boards render the form directly; the Apply click is optional.
            page.get_by_role("button", name="Apply", exact=True).first.click(timeout=5_000)
        page.wait_for_selector("#first_name", timeout=self.timeout_ms)
        page.wait_for_timeout(1_200)

    def _fill_core(self, page, profile: ApplicantProfile) -> None:
        page.fill("#first_name", profile.first_name)
        page.fill("#last_name", profile.last_name)
        if profile.preferred_name:
            page.fill("#preferred_name", profile.preferred_name)
        page.fill("#email", profile.email)

        self._choose(page, "#country", profile.country)
        page.fill("#phone", profile.phone)
        self._choose(page, "#candidate-location", profile.location)
        page.set_input_files("#resume", str(resolve_data_path(profile.resume_path)))

    def _fill_question(
        self,
        page,
        question: dict,
        profile: ApplicantProfile,
        company: str,
        title: str,
    ) -> None:
        label = str(question.get("label") or "")
        required = bool(question.get("required"))
        for field in question.get("fields") or []:
            if not isinstance(field, dict):
                continue
            name = str(field.get("name") or "")
            field_type = str(field.get("type") or "")
            if not name or field_type == "input_file":
                continue
            # Core identity fields are filled from the profile in _fill_core.
            if name in {
                "first_name",
                "last_name",
                "preferred_name",
                "email",
                "phone",
                "country",
                "candidate-location",
                "resume",
                "resume_text",
                "cover_letter",
                "cover_letter_text",
            }:
                continue

            answer = profile.answer(label, company=company, title=title)
            if answer is None:
                if required:
                    raise UnmappedQuestion(label)
                continue

            if field_type == "multi_value_single_select":
                self._choose(page, f"#{name}", answer)
            else:
                page.fill(f"#{name}", answer)

    @staticmethod
    def _retype(page, selector: str, value: str) -> None:
        page.click(selector)
        page.keyboard.press("Control+A")
        page.type(selector, value, delay=25)
        page.keyboard.press("Tab")

    @staticmethod
    def _choose(page, selector: str, value: str) -> None:
        page.click(selector)
        page.fill(selector, "")
        page.type(selector, value, delay=35)
        page.wait_for_timeout(500)
        try:
            page.get_by_role("option", name=value, exact=False).first.click(timeout=2_500)
        except Exception:  # noqa: BLE001 - fall back to keyboard selection
            page.keyboard.press("Enter")

    def _wait_for_confirmation(self, page) -> bool:
        try:
            page.wait_for_url("**/confirmation*", timeout=30_000)
            return True
        except Exception:  # noqa: BLE001
            pass
        try:
            page.wait_for_function(
                """() => {
                  const text = document.body.innerText.toLowerCase();
                  return text.includes("thank you for applying")
                    || text.includes("application has been submitted")
                    || text.includes("your application was submitted");
                }""",
                timeout=10_000,
            )
            return True
        except Exception:  # noqa: BLE001
            return False

    @staticmethod
    def _visible_error(page) -> str:
        selectors = [
            "[class*=error]",
            "[role=alert]",
            ".application--error",
            ".field-error",
        ]
        for selector in selectors:
            try:
                text = page.locator(selector).first.inner_text(timeout=1_000).strip()
                if text:
                    return text[:400]
            except Exception:  # noqa: BLE001
                continue
        return ""
