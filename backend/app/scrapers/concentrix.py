from __future__ import annotations

import json
from typing import Any

from bs4 import BeautifulSoup

from app.scrapers.base import FetchError
from app.scrapers.models import BaseScraper, JobRecord, ScrapeContext, ScrapeResult
from app.utils import clean_text, parse_date, safe_http_url

SEARCH_URL = "https://jobs.concentrix.com/job-search/?country=India"
FALLBACK_APPLY_URL = "https://jobs.concentrix.com/job-search/?country=India"


class ConcentrixScraper(BaseScraper):
    """Concentrix India — WordPress page with an embedded jobsData JSON blob.

    The public search page contains the full India job list (including
    descriptions), so a single request is enough. robots.txt asks for a
    10-second crawl delay; this scraper only makes that one request.
    """

    source = "concentrix"
    company = "Concentrix"
    default_max_pages = 1

    async def scrape(self, ctx: ScrapeContext) -> ScrapeResult:
        result = ScrapeResult()
        async with self.new_client() as http:
            response = await http.get(SEARCH_URL, fallback_html=True)
            soup = BeautifulSoup(response.text, "html.parser")
            script = soup.find("script", id="jobsData")
            if script is None:
                raise FetchError(SEARCH_URL, "Concentrix jobsData script not found")
            raw = script.string or script.get_text() or ""
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise FetchError(SEARCH_URL, f"invalid Concentrix jobsData: {exc}") from exc

            data = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(data, list):
                raise FetchError(SEARCH_URL, "unexpected Concentrix jobsData shape")

            for item in data:
                if self.limit_reached(ctx, result.seen_ids):
                    result.complete = False
                    result.note("Max jobs per source reached")
                    break
                job = self._parse_job(item)
                if job is None or job.external_id in result.seen_ids:
                    continue
                result.seen_ids.add(job.external_id)
                result.jobs.append(job)

        self.log.info(
            "Concentrix: found=%d, errors=%d, complete=%s",
            len(result.seen_ids),
            result.errors,
            result.complete,
        )
        return result

    def _parse_job(self, item: dict[str, Any]) -> JobRecord | None:
        if not isinstance(item, dict):
            return None
        external_id = str(
            item.get("ats_external_id") or item.get("id") or ""
        ).strip()
        title = clean_text(item.get("job_title"), max_length=500)
        if not external_id or not title:
            return None

        description = str(
            item.get("long_description")
            or item.get("short_description")
            or ""
        ).strip()
        if not description:
            return None

        location = clean_text(item.get("location"), max_length=1000)
        country = clean_text(item.get("country"), max_length=200)
        if country and country.lower() not in location.lower():
            location = f"{location}, {country}" if location else country

        apply_url = safe_http_url(
            item.get("apply_url")
            or item.get("landing_page_url")
            or FALLBACK_APPLY_URL,
            FALLBACK_APPLY_URL,
        )
        posted = parse_date(item.get("created_at")) or parse_date(item.get("updated_at"))

        return JobRecord(
            source=self.source,
            company=self.company,
            external_id=external_id,
            title=title,
            location=location,
            description_html=description,
            apply_url=apply_url,
            posted_date=posted,
        )
