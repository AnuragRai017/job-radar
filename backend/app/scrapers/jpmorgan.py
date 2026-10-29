from __future__ import annotations

from app.scrapers.oracle import OracleRecruitingScraper


class JPMorganScraper(OracleRecruitingScraper):
    """JPMorgan Chase — Oracle Recruiting Cloud public REST API."""

    source = "jpmorgan"
    company = "JPMorgan Chase"
    list_url = "https://jpmc.fa.oraclecloud.com/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
    detail_url = (
        "https://jpmc.fa.oraclecloud.com/hcmRestApi/resources/latest/"
        "recruitingCEJobRequisitionDetails"
    )
    site_number = "CX_1002"
    job_page = "https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1002/job"
    default_max_pages = 60
