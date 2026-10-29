from __future__ import annotations

from datetime import date, datetime
from typing import Any
from urllib.parse import urlencode

from app.scrapers.base import FetchError
from app.scrapers.models import BaseScraper, JobRecord, ScrapeContext, ScrapeResult
from app.utils import clean_text, safe_http_url

SEARCH_URL = "https://www.amazon.jobs/en/search.json"
PAGE_SIZE = 100
JOB_ROOT = "https://www.amazon.jobs"


class AmazonScraper(BaseScraper):
    """Amazon Jobs public search JSON, filtered to India.

    Each search result already contains the full description and qualification
    sections, so no detail requests are needed.
    """

    source = "amazon"
    company = "Amazon"
    default_max_pages = 30

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
                    "country": "IND",
                    "result_limit": PAGE_SIZE,
                    "offset": offset,
                    "sort": "recent",
                }
                url = f"{SEARCH_URL}?{urlencode(params)}"
                try:
                    response = await http.get(url, expect_json=True)
                except FetchError as exc:
                    if page_number == 1:
                        raise
                    result.complete = False
                    result.note(f"Search page {page_number} failed: {exc}")
                    self.log.warning(
                        "Amazon search page %d failed: %s", page_number, exc
                    )
                    break

                payload = response.json()
                if not isinstance(payload, dict):
                    raise FetchError(url, "unexpected Amazon search payload")
                jobs = payload.get("jobs") or []
                if not jobs:
                    break
                if total is None:
                    try:
                        total = int(payload.get("hits") or 0)
                    except (TypeError, ValueError):
                        total = 0
                    self.log.info("Amazon: %d India jobs reported", total)

                for item in jobs:
                    if self.limit_reached(ctx, result.seen_ids):
                        result.complete = False
                        result.note("Max jobs per source reached")
                        break
                    job = self._parse_job(item)
                    if job is None or job.external_id in result.seen_ids:
                        continue
                    result.seen_ids.add(job.external_id)
                    result.jobs.append(job)

                if self.limit_reached(ctx, result.seen_ids):
                    break
                offset += len(jobs)
                if total and offset >= total:
                    break
                if len(jobs) < PAGE_SIZE:
                    break
                page_number += 1
            else:
                result.complete = False
                result.note("Page limit reached")

        self.log.info(
            "Amazon: found=%d, errors=%d, complete=%s",
            len(result.seen_ids),
            result.errors,
            result.complete,
        )
        return result

    def _parse_job(self, item: dict[str, Any]) -> JobRecord | None:
        external_id = str(item.get("id") or item.get("id_icims") or "").strip()
        title = clean_text(item.get("title"), max_length=500)
        if not external_id or not title:
            return None

        parts: list[str] = []
        description = str(item.get("description") or "").strip()
        if description:
            parts.append(description)
        for key, heading in (
            ("basic_qualifications", "Basic Qualifications"),
            ("preferred_qualifications", "Preferred Qualifications"),
        ):
            value = str(item.get(key) or "").strip()
            if value:
                parts.append(f"<h3>{heading}</h3>\n{value}")
        if not parts:
            return None

        location = clean_text(item.get("location"), max_length=1000) or clean_text(
            item.get("normalized_location"), max_length=1000
        )
        job_path = str(item.get("job_path") or "").strip()
        apply_url = safe_http_url(f"{JOB_ROOT}{job_path}" if job_path else "", "")
        posted = _parse_amazon_date(item.get("posted_date"))

        return JobRecord(
            source=self.source,
            company=self.company,
            external_id=external_id,
            title=title,
            location=location,
            description_html="\n".join(parts),
            apply_url=apply_url,
            posted_date=posted,
        )


def _parse_amazon_date(value: object) -> date | None:
    text = clean_text(value)
    if not text:
        return None
    for fmt in ("%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None
