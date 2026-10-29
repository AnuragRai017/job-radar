from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from app.scrapers.base import FetchError
from app.scrapers.models import BaseScraper, JobRecord, ScrapeContext, ScrapeResult
from app.utils import clean_text, safe_http_url

SEARCH_URL = "https://gateway.mckinsey.com/apigw-x0cceuow60/v1/api/jobs/search"
JOB_PAGE = "https://www.mckinsey.com/careers/search-jobs/jobs"
PAGE_SIZE = 50


class McKinseyScraper(BaseScraper):
    """McKinsey & Company — public careers search JSON.

    The search response already contains the full role narrative, so no detail
    requests are required. ``countries=India`` can still return global roles
    that include an Indian city, so each document is filtered to roles whose
    country list contains India.
    """

    source = "mckinsey"
    company = "McKinsey & Company"
    default_max_pages = 10

    async def scrape(self, ctx: ScrapeContext) -> ScrapeResult:
        result = ScrapeResult()
        offset = 0
        page_number = 1
        total: int | None = None

        async with self.new_client() as http:
            while page_number <= self.max_pages(ctx):
                if self.limit_reached(ctx, result.seen_ids):
                    result.complete = False
                    result.note("Max jobs per source reached")
                    break

                params = {
                    "pageSize": PAGE_SIZE,
                    "start": offset + 1,
                    "countries": "India",
                    "lang": "en",
                }
                url = f"{SEARCH_URL}?{urlencode(params)}"
                try:
                    response = await http.get(
                        url,
                        expect_json=True,
                        headers={
                            "Accept": "application/json",
                            "Referer": "https://www.mckinsey.com/careers/search-jobs?countries=India",
                        },
                    )
                except FetchError as exc:
                    if page_number == 1:
                        raise
                    result.complete = False
                    result.note(f"Search page {page_number} failed: {exc}")
                    break

                payload = response.json()
                if not isinstance(payload, dict):
                    raise FetchError(url, "unexpected McKinsey search payload")
                docs = payload.get("docs") or []
                if total is None:
                    try:
                        total = int(payload.get("numFound") or 0)
                    except (TypeError, ValueError):
                        total = 0
                    self.log.info("McKinsey: %d India roles reported", total)
                if not docs:
                    break

                for doc in docs:
                    if self.limit_reached(ctx, result.seen_ids):
                        result.complete = False
                        result.note("Max jobs per source reached")
                        break
                    job = self._parse_job(doc)
                    if job is None or job.external_id in result.seen_ids:
                        continue
                    result.seen_ids.add(job.external_id)
                    result.jobs.append(job)

                offset += len(docs)
                if total and offset >= total:
                    break
                if len(docs) < PAGE_SIZE:
                    break
                page_number += 1
            else:
                result.complete = False
                result.note("Page limit reached")

        self.log.info(
            "McKinsey: found=%d, errors=%d, complete=%s",
            len(result.seen_ids),
            result.errors,
            result.complete,
        )
        return result

    def _parse_job(self, doc: dict[str, Any]) -> JobRecord | None:
        external_id = clean_text(doc.get("jobID"), max_length=80)
        title = clean_text(doc.get("title"), max_length=500)
        if not external_id or not title:
            return None

        countries = doc.get("countries") or []
        if isinstance(countries, list) and "India" not in countries:
            return None

        cities = doc.get("cities") or []
        india_cities: list[str] = []
        if isinstance(cities, list) and isinstance(countries, list):
            for city, country in zip(cities, countries, strict=False):
                if country == "India" and city and city not in india_cities:
                    india_cities.append(str(city))
        location = "; ".join(india_cities) or "India"

        parts: list[str] = []
        for heading, key in (
            ("Who you will work with", "whoYouWillWorkWith"),
            ("What you will do", "whatYouWillDo"),
            ("Your background", "yourBackground"),
            ("Salary and benefits", "jobSalaryBenefits"),
        ):
            value = str(doc.get(key) or "").strip()
            if value:
                parts.append(f"<h3>{heading}</h3>\n{value}")
        if not parts:
            return None

        friendly = clean_text(doc.get("friendlyURL"), max_length=300)
        apply_url = safe_http_url(
            f"{JOB_PAGE}/{friendly}" if friendly else doc.get("jobApplyURL"),
            "",
        )

        return JobRecord(
            source=self.source,
            company=self.company,
            external_id=external_id,
            title=title,
            location=location,
            description_html="\n".join(parts),
            apply_url=apply_url,
            posted_date=None,
        )
