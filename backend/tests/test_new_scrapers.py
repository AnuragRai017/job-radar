from __future__ import annotations

from datetime import date

from bs4 import BeautifulSoup

from app.scrapers.amazon import AmazonScraper, _parse_amazon_date
from app.scrapers.cadence import CadenceScraper
from app.scrapers.capgemini import CapgeminiIndiaScraper
from app.scrapers.concentrix import ConcentrixScraper
from app.scrapers.eightfold import HSBCScraper, MicrosoftScraper
from app.scrapers.exl import EXLScraper
from app.scrapers.greenhouse import MedallionScraper
from app.scrapers.registry import SCRAPER_CLASSES
from app.scrapers.successfactors import DeloitteIndiaScraper, EYScraper


def test_registry_has_all_fourteen_sources() -> None:
    assert set(SCRAPER_CLASSES) == {
        "jpmorgan",
        "deloitte",
        "capgemini",
        "google",
        "deloitte_india",
        "capgemini_india",
        "exl",
        "concentrix",
        "amazon",
        "microsoft",
        "medallion",
        "cadence",
        "hsbc",
        "ey",
    }


def test_successfactors_parses_listing_links_and_detail_fields() -> None:
    html = """
    <a class="jobTitle-link" href="/job/Bengaluru-Senior-Consultant/12345/">
      Senior Consultant
    </a>
    <a class="jobTitle-link" href="/job/Bengaluru-Senior-Consultant/12345/">
      Duplicate
    </a>
    <a class="jobTitle-link" href="/job/Mumbai-Analyst/67890/">
      Analyst
    </a>
    """
    listings = DeloitteIndiaScraper()._parse_listings(
        html, "https://southasiacareers.deloitte.com/search/"
    )
    assert [item.external_id for item in listings] == ["12345", "67890"]
    assert listings[0].title == "Senior Consultant"
    assert listings[0].url.endswith("/job/Bengaluru-Senior-Consultant/12345/")

    soup = BeautifulSoup("<h1>Job Title: Senior Consultant</h1>", "html.parser")
    assert DeloitteIndiaScraper._detail_title(soup) == "Senior Consultant"
    assert (
        DeloitteIndiaScraper._detail_location(
            "Location : Bengaluru • Entity : Deloitte Touche Tohmatsu India LLP"
        )
        == "Bengaluru"
    )
    assert (
        EYScraper._detail_location(
            "Location: London Other locations: Anywhere in Country Salary: Competitive"
        )
        == "London"
    )
    assert EYScraper._detail_date("Date: 29 Oct 2026") == date(2026, 10, 29)
    assert EYScraper._detail_date("Date: Oct 8, 2026") == date(2026, 10, 8)
    assert DeloitteIndiaScraper._is_closed("Sorry, this position has been filled.")
    assert not DeloitteIndiaScraper._is_closed("We are hiring for this position.")


def test_capgemini_india_uses_country_filter() -> None:
    scraper = CapgeminiIndiaScraper()
    assert scraper.company == "Capgemini India"
    url = scraper._list_url(2)
    assert "page=2" in url
    assert "country_code=in-en" in url
    assert scraper._detail_url("123-en_GB+test").endswith("/123-en_GB+test")


def test_exl_uses_oracle_cx2_site() -> None:
    scraper = EXLScraper()
    assert scraper.source == "exl"
    assert scraper.company == "EXL"
    assert scraper.site_number == "CX_2"
    assert scraper.location_filter == ",locationId=300000000467203"
    assert "fa-ewjt-saasfaprod1.fa.ocs.oraclecloud.com" in scraper.list_url


def test_eightfold_parses_hsbc_and_microsoft_payloads() -> None:
    hsbc = HSBCScraper()
    positions, total = hsbc._parse_list(
        {
            "positions": [{"id": 1, "name": "Engineer", "location": "Pune, India"}],
            "count": 1,
        }
    )
    assert total == 1
    assert positions[0]["name"] == "Engineer"

    microsoft = MicrosoftScraper()
    positions, total = microsoft._parse_list(
        {
            "data": {
                "positions": [
                    {
                        "id": 2,
                        "name": "Developer",
                        "locations": ["India", "Hyderabad, TS, India"],
                    }
                ],
                "count": 1,
            }
        }
    )
    assert total == 1
    assert microsoft._position_location(positions[0]) == "India; Hyderabad, TS, India"

    job = hsbc._parse_detail(
        {
            "id": 1,
            "name": "Engineer",
            "location": "Pune, India",
            "job_description": "<p>Build systems.</p>",
            "t_create": 1735689600,
            "canonicalPositionUrl": "https://portal.careers.hsbc.com/careers/job/1",
        },
        positions[0],
    )
    assert job.external_id == "1"
    assert job.location == "Pune, India"
    assert job.posted_date == date(2025, 1, 1)
    assert job.description_html == "<p>Build systems.</p>"

    job = microsoft._parse_detail(
        {
            "data": {
                "id": 2,
                "name": "Developer",
                "location": "Hyderabad, India",
                "jobDescription": "<p>Ship products.</p>",
                "postedTs": 1735689600,
                "publicUrl": "https://apply.careers.microsoft.com/careers/job/2",
            }
        },
        positions[0],
    )
    assert job.external_id == "2"
    assert job.title == "Developer"
    assert job.posted_date == date(2025, 1, 1)
    assert job.apply_url.endswith("/careers/job/2")


def test_greenhouse_unescapes_medallion_content() -> None:
    job = MedallionScraper()._parse_job(
        {
            "id": 4462782009,
            "title": "Customer Support Specialist",
            "location": {"name": "Remote"},
            "absolute_url": "https://job-boards.greenhouse.io/medallion/jobs/4462782009",
            "content": "&lt;p&gt;Help customers.&lt;/p&gt;",
            "updated_at": "2026-10-23T18:08:00-04:00",
        }
    )
    assert job is not None
    assert job.external_id == "4462782009"
    assert job.location == "Remote"
    assert job.description_html == "<p>Help customers.</p>"
    assert job.posted_date == date(2026, 10, 23)


def test_amazon_parses_india_job_and_date() -> None:
    job = AmazonScraper()._parse_job(
        {
            "id": "abc-123",
            "title": "Software Engineer",
            "location": "IN, KA, Bengaluru",
            "posted_date": "October 29, 2026",
            "job_path": "/en/jobs/10592064/software-engineer",
            "description": "<p>Build things.</p>",
            "basic_qualifications": "<ul><li>Python</li></ul>",
            "preferred_qualifications": "<ul><li>AWS</li></ul>",
        }
    )
    assert job is not None
    assert job.external_id == "abc-123"
    assert job.location == "IN, KA, Bengaluru"
    assert job.apply_url == "https://www.amazon.jobs/en/jobs/10592064/software-engineer"
    assert job.posted_date == date(2026, 10, 29)
    assert "<h3>Basic Qualifications</h3>" in job.description_html
    assert _parse_amazon_date("September 29, 2026") == date(2026, 9, 29)


def test_concentrix_parses_embedded_job_fields() -> None:
    job = ConcentrixScraper()._parse_job(
        {
            "id": "830",
            "job_title": "Team Leader",
            "location": "Mumbai, Mahārāshtra",
            "country": "India",
            "ats_external_id": "R1776807",
            "long_description": "<p>Lead a team.</p>",
            "apply_url": "https://cnx.wd1.myworkdayjobs.com/example",
            "created_at": "2026-10-28T14:05:49.560+08:00",
        }
    )
    assert job is not None
    assert job.external_id == "R1776807"
    assert job.location == "Mumbai, Mahārāshtra, India"
    assert job.apply_url.startswith("https://cnx.wd1.myworkdayjobs.com/")
    assert job.posted_date == date(2026, 10, 28)


def test_cadence_parses_listing_links_and_remote_location() -> None:
    html = """
    <a href="/jobs/ai-engineer">AI Engineer</a>
    <a href="/jobs/ai-engineer/">AI Engineer duplicate</a>
    <a href="/jobs/software-engineer-platform-all-levels">Software Engineer - Platform Remote</a>
    """
    listings = CadenceScraper()._parse_listings(
        html, "https://www.cadence.care/open-roles/business-and-tech"
    )
    assert [item.external_id for item in listings] == [
        "ai-engineer",
        "software-engineer-platform-all-levels",
    ]
    assert CadenceScraper._location("Remote-first team", "AI Engineer") == "Remote"
    assert CadenceScraper._location("Location: New York, NY", "AI Engineer") == "New York, NY"
