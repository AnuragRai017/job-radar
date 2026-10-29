from __future__ import annotations

import asyncio
from datetime import date
from typing import Any

from app.scrapers.ai_fallback import AiFallback, AiJob, AiListing
from app.scrapers.base import HttpResponse
from app.scrapers.deloitte import JOB_ID_RE, DeloitteScraper, Listing

BASE_URL = "https://apply.deloitte.com/en_US/careers/SearchJobs/"
HOSTS = {"apply.deloitte.com"}


def _fake_chat(fallback: AiFallback, response: Any) -> None:
    async def chat(_system: str, _prompt: str) -> Any:
        return response

    fallback._chat = chat  # type: ignore[method-assign]


def test_ai_parse_json_tolerates_fences_and_prose() -> None:
    assert AiFallback._parse_json('```json\n{"jobs": []}\n```') == {"jobs": []}
    assert AiFallback._parse_json('Here is the result: {"jobs": []} done') == {"jobs": []}
    assert AiFallback._parse_json('[{"jobs": []}]') == [{"jobs": []}]


def test_ai_compact_page_removes_scripts_and_chrome() -> None:
    html = """
    <html><body>
      <nav>Menu</nav>
      <main>
        <a href="/en_US/careers/JobDetail/Role/123" class="job">Role</a>
        <script>alert(1)</script>
      </main>
    </body></html>
    """
    compact = AiFallback._compact_page(html, 1_000)
    assert 'href="/en_US/careers/JobDetail/Role/123"' in compact
    assert "Role" in compact
    assert "alert(1)" not in compact
    assert "Menu" not in compact


def test_ai_disabled_and_budget_make_no_calls() -> None:
    disabled = AiFallback(enabled=False)
    assert disabled.available is False
    assert (
        asyncio.run(disabled.extract_job("<html></html>", page_url=BASE_URL)) is None
    )
    assert disabled.calls == 0

    exhausted = AiFallback(enabled=True, max_calls=0)
    assert exhausted.available is False
    assert (
        asyncio.run(
            exhausted.extract_listings(
                "<html></html>",
                base_url=BASE_URL,
                allowed_hosts=HOSTS,
                path_pattern=JOB_ID_RE,
            )
        )
        is None
    )
    assert exhausted.calls == 0


def test_ai_listing_validation_keeps_only_allowed_deloitte_urls() -> None:
    fallback = AiFallback(enabled=True)
    _fake_chat(
        fallback,
        {
            "jobs": [
                {
                    "url": "https://apply.deloitte.com/en_US/careers/JobDetail/Cyber/12345",
                    "title": "Cyber Consultant",
                    "location": "New York, NY",
                },
                {
                    "url": "https://evil.example/en_US/careers/JobDetail/Bad/99999",
                    "title": "Bad",
                    "location": "",
                },
                {
                    "url": "https://apply.deloitte.com/en_US/careers/Other/111",
                    "title": "Wrong path",
                    "location": "",
                },
                {
                    "url": "/en_US/careers/JobDetail/Relative/22222",
                    "title": "Relative Role",
                    "location": "",
                },
            ]
        },
    )

    listings = asyncio.run(
        fallback.extract_listings(
            "<html></html>",
            base_url=BASE_URL,
            allowed_hosts=HOSTS,
            path_pattern=JOB_ID_RE,
        )
    )

    assert listings == [
        AiListing(
            url="https://apply.deloitte.com/en_US/careers/JobDetail/Cyber/12345",
            title="Cyber Consultant",
            location="New York, NY",
        ),
        AiListing(
            url="https://apply.deloitte.com/en_US/careers/JobDetail/Relative/22222",
            title="Relative Role",
            location="",
        ),
    ]
    assert fallback.failures == 0


def test_ai_detail_sanitizes_html_and_parses_date() -> None:
    fallback = AiFallback(enabled=True)
    _fake_chat(
        fallback,
        {
            "title": "Senior Engineer",
            "location": "Chicago, IL",
            "description": (
                "<p>" + "Build reliable systems. " * 4 + "</p>"
                "<script>alert('x')</script><p>More responsibilities.</p>"
            ),
            "posted_date": "2025-03-04",
        },
    )

    job = asyncio.run(
        fallback.extract_job(
            "<html></html>",
            page_url="https://apply.deloitte.com/en_US/careers/JobDetail/Role/123",
            title_hint="Fallback title",
        )
    )

    assert job is not None
    assert job.title == "Senior Engineer"
    assert job.location == "Chicago, IL"
    assert job.posted_date == date(2025, 3, 4)
    assert "<script>" not in job.description_html
    assert "alert('x')" not in job.description_html
    assert "More responsibilities." in job.description_html


def test_ai_rejects_short_detail_and_opens_circuit() -> None:
    fallback = AiFallback(enabled=True)
    calls = 0

    async def chat(_system: str, _prompt: str) -> Any:
        nonlocal calls
        calls += 1
        return {"title": "Too short", "description": "nope"}

    fallback._chat = chat  # type: ignore[method-assign]

    for _ in range(AiFallback.MAX_CONSECUTIVE_FAILURES + 1):
        assert (
            asyncio.run(
                fallback.extract_job(
                    "<html></html>",
                    page_url="https://apply.deloitte.com/en_US/careers/JobDetail/Role/123",
                )
            )
            is None
        )

    assert calls == AiFallback.MAX_CONSECUTIVE_FAILURES
    assert fallback.circuit_open is True
    assert fallback.available is False


def test_deloitte_ai_listing_conversion_and_detail_fallback() -> None:
    scraper = DeloitteScraper()
    listings = scraper._listings_from_ai(
        [
            AiListing(
                url=(
                    "https://apply.deloitte.com/en_US/careers/"
                    "JobDetail/Security/12345"
                ),
                title=" Security Consultant ",
                location="",
            )
        ]
    )
    assert listings == [
        Listing(
            external_id="12345",
            title="Security Consultant",
            location="Multiple Locations",
            url="https://apply.deloitte.com/en_US/careers/JobDetail/Security/12345",
        )
    ]

    class FakeHttp:
        async def get(self, url: str, **_kwargs: Any) -> HttpResponse:
            return HttpResponse(
                url=url,
                status_code=200,
                text="<html><body><h1>No JSON-LD here</h1></body></html>",
            )

    class FakeAi:
        enabled = True

        async def extract_job(self, _html: str, **_kwargs: Any) -> AiJob:
            return AiJob(
                title="AI Title",
                location="AI Location",
                description_html="<p>" + "Useful description. " * 4 + "</p>",
                posted_date=date(2025, 4, 5),
            )

    job, used_ai = asyncio.run(
        scraper._fetch_detail(FakeHttp(), listings[0], ai=FakeAi())  # type: ignore[arg-type]
    )
    assert used_ai is True
    assert job.external_id == "12345"
    assert job.title == "Security Consultant"
    assert job.location == "AI Location"
    assert job.posted_date == date(2025, 4, 5)
    assert "Useful description." in job.description_html
