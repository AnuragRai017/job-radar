from __future__ import annotations

from typing import Any

from app.scrapers.base import FetchError, HttpClient
from app.scrapers.models import BaseScraper, JobRecord, ScrapeContext, ScrapeResult
from app.utils import clean_text, parse_date, safe_http_url

ALLSTATE_API_URL = "https://jobsapi-internal.m-cloud.io/api/job"
ALLSTATE_ORGANIZATION = "2030"
ALLSTATE_PORTAL = "Workday-MuleAPI-External"
ALLSTATE_FALLBACK_URL = "https://www.allstate.jobs/job-search-results/"


class AllstateScraper(BaseScraper):
    """Allstate — CWS (m-cloud.io) search API backing allstate.jobs.

    The WordPress site calls this JSON endpoint with the organization id from
    its public ``cws_opts`` config. ``SearchText=India`` returns the India
    postings, and ``offset`` paginates the result set.
    """

    source = "allstate"
    company = "Allstate"
    page_size = 100
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

                try:
                    records, page_total = await self._fetch_page(http, offset)
                except (FetchError, ValueError, KeyError) as exc:
                    if page_number == 1:
                        raise
                    result.complete = False
                    result.note(f"Search page {page_number} failed: {exc}")
                    self.log.warning(
                        "%s search page %d failed: %s", self.company, page_number, exc
                    )
                    break

                if total is None:
                    total = page_total
                    self.log.info("%s: %d India roles reported", self.company, total)
                if not records:
                    break

                for record in records:
                    if self.limit_reached(ctx, result.seen_ids):
                        result.complete = False
                        result.note("Max jobs per source reached")
                        break

                    external_id = clean_text(record.get("id"), max_length=80)
                    if not external_id or external_id in result.seen_ids:
                        continue

                    title = clean_text(record.get("title"), max_length=500) or "Untitled role"
                    location = self._location(record)
                    posted = parse_date(record.get("open_date"))
                    result.seen_ids.add(external_id)

                    if not self.needs_detail(
                        ctx,
                        external_id,
                        title=title,
                        location=location,
                        posted_date=posted,
                    ):
                        continue

                    try:
                        job = self._parse_job(record, external_id)
                    except ValueError as exc:
                        result.errors += 1
                        self.log.warning(
                            "%s skipped %s: %s", self.company, external_id, exc
                        )
                        continue

                    result.details_fetched += 1
                    result.jobs.append(job)

                offset += len(records)
                if total and offset >= total:
                    break
                if len(records) < self.page_size:
                    break
                page_number += 1
            else:
                result.complete = False
                result.note("Page limit reached")

        self.log.info(
            "%s: found=%d, collected=%d, errors=%d, complete=%s",
            self.company,
            len(result.seen_ids),
            result.details_fetched,
            result.errors,
            result.complete,
        )
        return result

    async def _fetch_page(
        self, http: HttpClient, offset: int
    ) -> tuple[list[dict[str, Any]], int]:
        response = await http.get(
            self._page_url(offset),
            expect_json=True,
            headers={"Accept": "application/json", "Referer": "https://www.allstate.jobs/"},
        )
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("unexpected Allstate payload")
        rows = payload.get("queryResult") or []
        if not isinstance(rows, list):
            raise ValueError("Allstate payload has no queryResult list")
        try:
            total = int(payload.get("totalHits") or 0)
        except (TypeError, ValueError):
            total = 0
        return rows, total

    def _page_url(self, offset: int) -> str:
        return (
            f"{ALLSTATE_API_URL}?organization={ALLSTATE_ORGANIZATION}"
            f"&portalid={ALLSTATE_PORTAL}&limit={self.page_size}"
            f"&SearchText=India&offset={offset}"
        )

    def _parse_job(self, record: dict[str, Any], external_id: str) -> JobRecord:
        description = str(record.get("description") or "").strip()
        if not description:
            raise ValueError("record has no description")
        title = clean_text(record.get("title"), max_length=500) or "Untitled role"
        job_code = clean_text(record.get("ref") or record.get("clientid"), max_length=80)
        apply_url = safe_http_url(
            record.get("url"), f"{ALLSTATE_FALLBACK_URL}?compliment[]=India"
        )
        return JobRecord(
            source=self.source,
            company=self.company,
            external_id=external_id,
            job_code=job_code or external_id,
            title=title,
            location=self._location(record),
            description_html=description,
            apply_url=apply_url,
            posted_date=parse_date(record.get("open_date")),
        )

    @staticmethod
    def _location(record: dict[str, Any]) -> str:
        city = clean_text(record.get("primary_city"), max_length=200)
        state = clean_text(record.get("primary_state"), max_length=200)
        country = clean_text(record.get("primary_country"), max_length=80)
        if country.upper() == "IN":
            country = "India"
        parts = [part for part in (city, state, country) if part]
        return ", ".join(parts)
