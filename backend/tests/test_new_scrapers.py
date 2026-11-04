from __future__ import annotations

import base64
import json
from datetime import date

from bs4 import BeautifulSoup
from nacl.public import Box, PrivateKey

from app.scrapers.allstate import AllstateScraper
from app.scrapers.amazon import AmazonScraper, _parse_amazon_date
from app.scrapers.bcg import BCGScraper
from app.scrapers.cadence import CadenceScraper
from app.scrapers.capgemini import CapgeminiIndiaScraper
from app.scrapers.concentrix import ConcentrixScraper
from app.scrapers.eightfold import HSBCScraper, MicrosoftScraper, VodafoneScraper
from app.scrapers.exl import EXLScraper
from app.scrapers.greenhouse import HubSpotScraper, MedallionScraper
from app.scrapers.ibm import IBMScraper
from app.scrapers.kpmg_gs import KPMGGlobalServicesScraper
from app.scrapers.mckinsey import McKinseyScraper
from app.scrapers.registry import SCRAPER_CLASSES
from app.scrapers.successfactors import DeloitteIndiaScraper, EYScraper
from app.scrapers.talentrecruit import SECRET_KEY, KPMGIndiaScraper
from app.scrapers.workday import PwCIndiaScraper, ThreeMScraper


def test_registry_has_all_twenty_four_sources() -> None:
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
        "kpmg_gs",
        "kpmg_india",
        "mckinsey",
        "pwc_india",
        "vodafone",
        "ibm",
        "3m",
        "bcg",
        "allstate",
        "hubspot",
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
    assert (
        scraper._job_code(
            "https://careers.capgemini.com/job/Bangalore-Data-Engineer/1444351833/?x=1",
            "123-en_GB+test",
        )
        == "1444351833"
    )


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
            "display_job_id": 65139,
            "canonicalPositionUrl": "https://portal.careers.hsbc.com/careers/job/1",
        },
        positions[0],
    )
    assert job.external_id == "1"
    assert job.job_code == "65139"
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
                "displayJobId": 200044512,
                "publicUrl": "https://apply.careers.microsoft.com/careers/job/2",
            }
        },
        positions[0],
    )
    assert job.external_id == "2"
    assert job.job_code == "200044512"
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
            "id_icims": "10592064",
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
    assert job.job_code == "10592064"
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


def test_kpmg_gs_uses_cx3_india_facet() -> None:
    scraper = KPMGGlobalServicesScraper()
    assert scraper.source == "kpmg_gs"
    assert scraper.company == "KPMG Global Services"
    assert scraper.site_number == "CX_3"
    assert scraper.location_filter == ",locationId=300000000296042"
    assert "ejgk.fa.em2.oraclecloud.com" in scraper.list_url


def test_mckinsey_parses_india_job_and_filters_other_countries() -> None:
    scraper = McKinseyScraper()
    job = scraper._parse_job(
        {
            "jobID": "14918",
            "title": "Data Engineer",
            "countries": ["India", "United States"],
            "cities": ["Bengaluru", "New York City"],
            "friendlyURL": "dataengineer-14918",
            "whoYouWillWorkWith": "<div>Work with teams.</div>",
            "whatYouWillDo": "<div>Build data products.</div>",
            "yourBackground": "<ul><li>Python and SQL.</li></ul>",
        }
    )
    assert job is not None
    assert job.external_id == "14918"
    assert job.location == "Bengaluru"
    assert job.apply_url == "https://www.mckinsey.com/careers/search-jobs/jobs/dataengineer-14918"
    assert "What you will do" in job.description_html
    assert "Python and SQL" in job.description_html

    assert (
        scraper._parse_job(
            {
                "jobID": "2",
                "title": "Consultant",
                "countries": ["United States"],
                "cities": ["New York City"],
                "friendlyURL": "consultant-2",
                "whoYouWillWorkWith": "<div>Team.</div>",
            }
        )
        is None
    )


def test_pwc_workday_listing_fields() -> None:
    scraper = PwCIndiaScraper()
    assert scraper.site == "Global_Experienced_Careers"
    listing = scraper._parse_listing(
        {
            "title": "Data Engineer",
            "externalPath": "/job/Bengaluru-Millenia/Data-Engineer_123WD",
            "locationsText": "Bengaluru Millenia",
            "bulletFields": ["123WD"],
        }
    )
    assert listing is not None
    assert listing.external_id == "123WD"
    assert listing.location == "Bengaluru Millenia"
    assert listing.path == "/job/Bengaluru-Millenia/Data-Engineer_123WD"


def test_kpmg_talentrecruit_decrypts_and_parses_job() -> None:
    server = PrivateKey.generate()
    client_public = PrivateKey(SECRET_KEY).public_key
    box = Box(server, client_public)
    nonce = b"0" * 24
    plaintext = json.dumps(
        {
            "error": False,
            "statusCode": 200,
            "data": {
                "data": {
                    "noOfTotalRecords": {"count": 1},
                    "data": [
                        {
                            "jobid": "abc123",
                            "code": "5257",
                            "title": "Data Engineer",
                            "joblocation": "Bengaluru, Karnataka, India",
                            "country": "India",
                            "publishedtime": "2026-10-29T06:42:59.000Z",
                            "description": "<p>Build pipelines.</p>",
                            "qualificationcriteria": "<p>Python and SQL.</p>",
                        }
                    ],
                }
            },
        }
    ).encode()
    encrypted = box.encrypt(plaintext, nonce)
    payload = {
        "text": base64.b64encode(encrypted.ciphertext).decode(),
        "iv": base64.b64encode(bytes(server.public_key)).decode(),
        "key": base64.b64encode(nonce).decode(),
    }
    decrypted = KPMGIndiaScraper._decrypt(payload)
    inner = KPMGIndiaScraper._inner_payload(decrypted)
    assert inner["noOfTotalRecords"]["count"] == 1

    job = KPMGIndiaScraper()._parse_job(inner["data"][0])
    assert job is not None
    assert job.external_id == "5257"
    assert job.title == "Data Engineer"
    assert job.location == "Bengaluru, Karnataka, India"
    assert job.posted_date == date(2026, 10, 29)
    assert "Qualifications" in job.description_html
    assert job.apply_url.endswith("/career-page/apply/abc123")

    fallback = KPMGIndiaScraper()._parse_job({**inner["data"][0], "code": ""})
    assert fallback is not None
    assert fallback.external_id == "abc123"


def test_vodafone_reuses_pcsx_without_csrf() -> None:
    scraper = VodafoneScraper()
    assert scraper.source == "vodafone"
    assert scraper.company == "Vodafone"
    assert scraper.domain == "vodafone.com"
    assert scraper.requires_csrf is False
    assert "/api/pcsx/search" in scraper._list_url(20)
    assert "domain=vodafone.com" in scraper._list_url(20)
    assert "start=20" in scraper._list_url(20)
    assert "x-csrf-token" not in scraper._api_headers()

    job = scraper._parse_detail(
        {
            "data": {
                "id": 563018698166833,
                "displayJobId": "291175",
                "name": "VP - Culture, DEI, EVP & Internal Communications Lead",
                "location": "India",
                "jobDescription": "<p>Lead communications.</p>",
                "postedTs": 1791270450,
                "publicUrl": "https://jobs.vodafone.com/careers/job/563018698166833",
            }
        },
        {"id": 563018698166833, "name": "VP", "locations": ["India"]},
    )
    assert job.external_id == "563018698166833"
    assert job.job_code == "291175"
    assert job.location == "India"
    assert job.apply_url.endswith("/careers/job/563018698166833")
    assert job.description_html == "<p>Lead communications.</p>"


def test_ibm_paginates_india_search_hits() -> None:
    scraper = IBMScraper()
    assert scraper.source == "ibm"
    assert scraper.page_size == 100
    query = scraper._query(200, 3)
    assert query["from"] == 200
    assert query["p"] == 3
    assert query["query"]["bool"]["must"] == [{"match": {"country": "in"}}]
    assert query["sort"] == [{"_id": "asc"}]
    assert "raw_body" in query["_source"]

    payload = {
        "hits": {
            "total": {"value": 1257, "relation": "eq"},
            "hits": [
                {
                    "_source": {
                        "title": "India – Quality Leader",
                        "url": "https://careers.ibm.com/careers/JobDetail?jobId=139038",
                        "field_keyword_19": "Bangalore, IN",
                        "dcdate": "2026-10-28",
                        "raw_body": "<p>Youâ€™ll build quality systems.</p>",
                    }
                },
                {
                    "_source": {
                        "title": "Consultant",
                        "url": "https://careers.ibm.com/careers/JobDetail?jobId=139039",
                        "field_keyword_19": "",
                        "dcdate": "2026-10-27",
                        "description": "Short description.",
                    }
                },
                "not-a-hit",
            ],
        }
    }
    records, total = scraper._parse_page(payload)
    assert total == 1257
    assert len(records) == 2

    assert scraper._external_id(records[0]) == "139038"
    job = scraper._parse_job(records[0], "139038")
    assert job.external_id == "139038"
    assert job.job_code == "139038"
    assert job.title == "Quality Leader"
    assert job.location == "Bangalore, IN"
    assert job.posted_date == date(2026, 10, 28)
    assert job.apply_url.endswith("jobId=139038")
    assert job.description_html == "<p>You’ll build quality systems.</p>"

    fallback = scraper._parse_job(records[1], "139039")
    assert fallback.location == "India"
    assert fallback.description_html == "Short description."

    assert scraper._external_id({"url": "https://careers.ibm.com/careers/JobDetail"}) == ""
    assert scraper._repair_text("plain text") == "plain text"


def test_3m_uses_workday_country_facet() -> None:
    scraper = ThreeMScraper()
    assert scraper.source == "3m"
    assert scraper.company == "3M"
    assert scraper.tenant == "3m"
    assert scraper.site == "Search"
    assert scraper.applied_facet_key == "Location_Country"
    assert scraper.india_facet_id == "c4f78be1a8f14da0ab49ce1162348a5e"
    assert scraper._jobs_url() == "https://3m.wd1.myworkdayjobs.com/wday/cxs/3m/Search/jobs"
    assert scraper._detail_url("/job/IN-Bangalore/Role_R01170069").endswith(
        "/wday/cxs/3m/Search/job/IN-Bangalore/Role_R01170069"
    )


def test_bcg_parses_embedded_results_and_jobposting() -> None:
    search_html = """
    <script>
      phApp = phApp || {};
      phApp.ddo = {"eagerLoadRefineSearch":{"status":200,"totalHits":2,"data":{"jobs":[
        {"jobId":"60181","title":"India Specialist Marketing - CRM","country":"India","location":"Gurgaon, Haryana, India","postedDate":"2026-10-21T00:00:00.000+0000"},
        {"jobId":"61334","title":"Administrative Assistant","country":"Portugal","location":"Lisbon, Portugal","postedDate":"2026-09-14T00:00:00.000+0000"}
      ]}}};
    </script>
    """
    listings, total = BCGScraper._parse_search(search_html)
    assert total == 2
    assert [item["jobId"] for item in listings] == ["60181", "61334"]

    detail_html = """
    <script type="application/ld+json">
    {"@type":"JobPosting","identifier":{"@type":"PropertyValue","value":"60181"},
     "title":"India Specialist Marketing - CRM","datePosted":"2026-11-04",
     "jobLocation":{"@type":"Place","address":{"@type":"PostalAddress",
       "addressLocality":"Gurgaon","addressRegion":"Haryana","addressCountry":"India"}},
     "description":"<p>Own CRM campaigns.</p>"}
    </script>
    """
    job = BCGScraper()._parse_detail(detail_html, listings[0])
    assert job.external_id == "60181"
    assert job.job_code == "60181"
    assert job.title == "India Specialist Marketing - CRM"
    assert job.location == "Gurgaon, Haryana, India"
    assert job.posted_date == date(2026, 10, 21)
    assert job.description_html == "<p>Own CRM campaigns.</p>"
    assert job.apply_url.endswith("/global/en/job/60181")


def test_allstate_parses_india_record() -> None:
    scraper = AllstateScraper()
    assert scraper.source == "allstate"
    assert "organization=2030" in scraper._page_url(100)
    assert "SearchText=India" in scraper._page_url(100)
    assert "offset=100" in scraper._page_url(100)

    job = scraper._parse_job(
        {
            "id": 24083945,
            "ref": "R36353",
            "title": "Software Engineer Senior Consultant I",
            "description": "<p>Build systems.</p>",
            "primary_city": "Bengaluru",
            "primary_state": "Karnataka",
            "primary_country": "IN",
            "open_date": "2026-10-29T00:00:00Z",
            "url": "https://www.allstate.jobs/job/24083945/software-engineer/",
        },
        "24083945",
    )
    assert job.external_id == "24083945"
    assert job.job_code == "R36353"
    assert job.location == "Bengaluru, Karnataka, India"
    assert job.posted_date == date(2026, 10, 29)
    assert job.description_html == "<p>Build systems.</p>"


def test_hubspot_filters_greenhouse_board_to_india() -> None:
    scraper = HubSpotScraper()
    assert scraper.source == "hubspot"
    assert scraper.board_token == "hubspotjobs"
    assert scraper._include({"location": {"name": "Office - Bengaluru, India"}})
    assert not scraper._include({"location": {"name": "Remote - USA"}})

    job = scraper._parse_job(
        {
            "id": 5990863,
            "title": "Account Executive, Mid Market",
            "location": {"name": "Office - Bengaluru, India"},
            "absolute_url": "https://www.hubspot.com/careers/jobs/5990863?gh_jid=5990863",
            "content": "&lt;p&gt;Sell HubSpot.&lt;/p&gt;",
            "updated_at": "2026-10-30T10:00:00-04:00",
        }
    )
    assert job is not None
    assert job.external_id == "5990863"
    assert job.job_code == "5990863"
    assert job.location == "Office - Bengaluru, India"
    assert job.description_html == "<p>Sell HubSpot.</p>"
