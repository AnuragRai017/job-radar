from __future__ import annotations

from app.scrapers.oracle import OracleRecruitingScraper

KPMG_HOST = "https://ejgk.fa.em2.oraclecloud.com"


class KPMGGlobalServicesScraper(OracleRecruitingScraper):
    """KPMG Global Services — Oracle Recruiting Cloud site CX_3."""

    source = "kpmg_gs"
    company = "KPMG Global Services"
    list_url = f"{KPMG_HOST}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
    detail_url = (
        f"{KPMG_HOST}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails"
    )
    site_number = "CX_3"
    # India country facet from the public CX_3 search page.
    location_filter = ",locationId=300000000296042"
    job_page = f"{KPMG_HOST}/hcmUI/CandidateExperience/en/sites/CX_3/job"
    default_max_pages = 20
