from __future__ import annotations

from app.scrapers.oracle import OracleRecruitingScraper

EXL_HOST = "https://fa-ewjt-saasfaprod1.fa.ocs.oraclecloud.com"


class EXLScraper(OracleRecruitingScraper):
    """EXL — Oracle Recruiting Cloud public REST API (CX_2 site)."""

    source = "exl"
    company = "EXL"
    list_url = (
        f"{EXL_HOST}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
    )
    detail_url = (
        f"{EXL_HOST}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails"
    )
    site_number = "CX_2"
    # Same India country facet used by the public CX_2 search page.
    location_filter = ",locationId=300000000467203"
    job_page = f"{EXL_HOST}/hcmUI/CandidateExperience/en/sites/CX_2/job"
    default_max_pages = 40
