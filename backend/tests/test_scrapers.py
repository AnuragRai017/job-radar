from __future__ import annotations

import json
from datetime import date

from bs4 import BeautifulSoup

from app.scrapers.capgemini import CapgeminiScraper
from app.scrapers.deloitte import DeloitteScraper
from app.scrapers.google import GoogleScraper
from app.scrapers.jpmorgan import JPMorganScraper
from app.scrapers.models import KnownJob, ScrapeContext

GOOGLE_RECORD = [
    "101",  # 0: external id
    "Software Engineer",  # 1: title
    "/url",  # 2: gated apply path
    ["<ul><li>Build things</li></ul>"],  # 3: responsibilities
    ["<ul><li>BS degree</li></ul>"],  # 4: qualifications
    None,
    None,
    None,
    None,
    [["New York, NY, USA"], ["London, UK"]],  # 9: locations
    ["<p>About the team</p>"],  # 10: about
    None,
    [1735689600, 0],  # 12: posted timestamp
    None,
    None,
    None,
    None,
    None,
    "<p>Remote note</p>",  # 18: location note
]

GOOGLE_HTML = (
    "<html><body><script>"
    "AF_initDataCallback({key: 'ds:1', hash: 'abc123', data: "
    + json.dumps([[GOOGLE_RECORD], None, 1])
    + ", sideChannel: {}});"
    "</script></body></html>"
)


def test_google_extracts_balanced_ds1_blob() -> None:
    html = (
        "<script>AF_initDataCallback({key: 'ds:1', hash: 'h', data: "
        '[["a ] inside a string", 2], null, 3], sideChannel: {}});</script>'
    )
    data = GoogleScraper._extract_ds1(html)
    assert data == [["a ] inside a string", 2], None, 3]
    assert GoogleScraper._extract_ds1("<html>no payload</html>") is None


def test_google_parses_job_record() -> None:
    data = GoogleScraper._extract_ds1(GOOGLE_HTML)
    assert data is not None
    record = data[0][0]

    job = GoogleScraper()._parse_record(record)
    assert job is not None
    assert job.external_id == "101"
    assert job.title == "Software Engineer"
    assert job.location == "New York, NY, USA; London, UK"
    assert job.posted_date == date(2025, 1, 1)
    assert job.apply_url.endswith("/101")
    assert "<h2>About the job</h2>" in job.description_html
    assert "Build things" in job.description_html
    assert "Remote note" in job.description_html


def test_google_rejects_incomplete_records() -> None:
    scraper = GoogleScraper()
    assert scraper._parse_record(["101", "Title"]) is None
    assert scraper._parse_record([["not-a-list"]]) is None


def test_deloitte_listing_discovery() -> None:
    html = """
    <article class="article--result">
      <a href="/en_US/careers/JobDetail/Cyber-Consultant/12345">
        Cyber Data Privacy Consultant
      </a>
      <div class="article__header__text__subtitle">
        <span>Consulting</span><span>New York, NY</span>
      </div>
    </article>
    <article class="article--result">
      <a href="/en_US/careers/JobDetail/Duplicate/12345">Duplicate</a>
    </article>
    <article class="article--result"><a href="/en_US/careers/Other/9">Skip</a></article>
    """
    listings = DeloitteScraper()._parse_listings(
        html, "https://apply.deloitte.com/en_US/careers/SearchJobs/"
    )
    assert len(listings) == 1
    listing = listings[0]
    assert listing.external_id == "12345"
    assert listing.title == "Cyber Data Privacy Consultant"
    assert listing.location == "New York, NY"
    assert listing.url == (
        "https://apply.deloitte.com/en_US/careers/JobDetail/Cyber-Consultant/12345"
    )


def test_deloitte_extracts_job_posting_from_graph() -> None:
    html = """
    <script type="application/ld+json">
    {
      "@context": "https://schema.org",
      "@graph": [
        {"@type": "WebPage", "name": "Careers"},
        {
          "@type": ["JobPosting"],
          "title": "Cyber Consultant",
          "description": "<p>Short</p>",
          "datePosted": "2025-02-03"
        }
      ]
    }
    </script>
    <script type="application/ld+json">
    {"@type": "JobPosting", "description": "<p>A much longer description for the role.</p>",
     "datePosted": "2025-02-03"}
    </script>
    """
    soup = BeautifulSoup(html, "html.parser")
    posting = DeloitteScraper._extract_job_posting(soup)
    assert posting is not None
    assert "much longer description" in posting["description"]

    locations = BeautifulSoup(
        """
        <div class="article__header--locations">
          <p>New York, NY</p><p>Chicago, IL</p><p>New York, NY</p>
        </div>
        """,
        "html.parser",
    )
    assert DeloitteScraper._detail_location(locations) == "New York, NY; Chicago, IL"


def test_capgemini_renders_sections_and_escapes_content() -> None:
    payload = {
        "title": "Senior Kotlin Engineer",
        "sections": [
            {"heading": "About the role", "content": "Line one\nLine two\n\nSecond paragraph"},
            {"heading": "Senior Kotlin Engineer", "content": "No duplicate heading"},
            {"heading": "Safety", "content": "<script>alert(1)</script> & more"},
            {"heading": None, "content": ""},
        ],
    }
    rendered = CapgeminiScraper._description_html(payload, "Senior Kotlin Engineer")
    assert "<h3>About the role</h3>" in rendered
    assert "<h3>Senior Kotlin Engineer</h3>" not in rendered
    assert "Line one<br>Line two" in rendered
    assert "<p>Second paragraph</p>" in rendered
    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered
    assert "&amp; more" in rendered

    fallback = CapgeminiScraper._description_html(
        {"content": "Only content", "title": "T"}, "T"
    )
    assert fallback == "<p>Only content</p>"
    assert CapgeminiScraper._description_html({"summary": ""}, "T") == ""


def test_jpmorgan_location_deduplication() -> None:
    item = {
        "PrimaryLocation": "New York, NY",
        "secondaryLocations": [
            {"LocationName": "new york, ny"},
            {"Name": "London, UK"},
            "ignore-me",
        ],
    }
    assert JPMorganScraper._list_location(item) == "New York, NY; London, UK"

    detail = {
        "PrimaryLocation": "Cebu, Philippines",
        "otherWorkLocations": [{"Location": "Manila, Philippines"}],
        "secondaryLocations": [{"LocationName": "Cebu, Philippines"}],
    }
    assert JPMorganScraper._detail_location(detail) == "Cebu, Philippines; Manila, Philippines"


def test_needs_detail_skips_unchanged_jobs_with_descriptions() -> None:
    scraper = DeloitteScraper()
    known = {
        "1": KnownJob(
            title="Consultant",
            location="New York, NY",
            posted_date=date(2025, 1, 1),
            has_description=True,
        )
    }
    ctx = ScrapeContext(known=known)

    assert (
        scraper.needs_detail(
            ctx,
            "1",
            title="Consultant",
            location="New York, NY",
            posted_date=date(2025, 1, 1),
            compare_location=True,
        )
        is False
    )
    assert scraper.needs_detail(ctx, "2", title="Consultant") is True
    assert scraper.needs_detail(ctx, "1", title="Senior Consultant") is True
    assert (
        scraper.needs_detail(ctx, "1", title="Consultant", posted_date=date(2025, 2, 1))
        is True
    )
    assert (
        scraper.needs_detail(
            ctx, "1", title="Consultant", location="London, UK", compare_location=True
        )
        is True
    )
    # Avature uses placeholder locations that should not trigger detail requests.
    assert (
        scraper.needs_detail(
            ctx,
            "1",
            title="Consultant",
            location="Multiple Locations",
            compare_location=True,
        )
        is False
    )
