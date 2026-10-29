from __future__ import annotations

import html
from typing import Any

from app.scrapers.base import FetchError
from app.scrapers.models import BaseScraper, JobRecord, ScrapeContext, ScrapeResult
from app.utils import clean_text, parse_date, safe_http_url

GREENHOUSE_API = "https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true"


class GreenhouseScraper(BaseScraper):
    """Public Greenhouse job-board API (board jobs include full content)."""

    board_token = ""
    default_max_pages = 1

    async def scrape(self, ctx: ScrapeContext) -> ScrapeResult:
        result = ScrapeResult()
        url = GREENHOUSE_API.format(board=self.board_token)

        async with self.new_client() as http:
            response = await http.get(url, expect_json=True)
            payload = response.json()
            if not isinstance(payload, dict):
                raise FetchError(url, "unexpected Greenhouse payload")
            jobs = payload.get("jobs") or []

            for item in jobs:
                if self.limit_reached(ctx, result.seen_ids):
                    result.complete = False
                    result.note("Max jobs per source reached")
                    break
                external_id = str(item.get("id") or "").strip()
                if not external_id or external_id in result.seen_ids:
                    continue
                result.seen_ids.add(external_id)
                job = self._parse_job(item)
                if job is None:
                    result.errors += 1
                    result.complete = False
                    continue
                result.jobs.append(job)

        self.log.info(
            "%s: found=%d, errors=%d, complete=%s",
            self.company,
            len(result.seen_ids),
            result.errors,
            result.complete,
        )
        return result

    def _parse_job(self, item: dict[str, Any]) -> JobRecord | None:
        external_id = str(item.get("id") or "").strip()
        title = clean_text(item.get("title"), max_length=500)
        if not external_id or not title:
            return None

        description = str(item.get("content") or "")
        if description:
            description = html.unescape(description)
        if not description:
            return None

        location_data = item.get("location")
        location = ""
        if isinstance(location_data, dict):
            location = clean_text(location_data.get("name"), max_length=1000)
        elif location_data:
            location = clean_text(location_data, max_length=1000)

        apply_url = safe_http_url(item.get("absolute_url"), "")
        posted = parse_date(item.get("first_published")) or parse_date(
            item.get("updated_at")
        )

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


class MedallionScraper(GreenhouseScraper):
    source = "medallion"
    company = "Medallion"
    board_token = "medallionakafirstlayerai"
