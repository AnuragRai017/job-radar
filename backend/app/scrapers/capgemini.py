from __future__ import annotations

import html as html_module
import math
import re
from typing import Any
from urllib.parse import quote

from app.sanitize import text_to_html
from app.scrapers.base import FetchError, HttpClient
from app.scrapers.models import BaseScraper, JobRecord, ScrapeContext, ScrapeResult
from app.utils import clean_text, parse_date, safe_http_url

LIST_URL = "https://www.capgemini.com/wp-json/llm/v1/jobs"
PAGE_SIZE = 50


class CapgeminiScraper(BaseScraper):
    """Capgemini — public WordPress JobStream JSON API.

    Capgemini's live global job search is powered by
    /wp-json/llm/v1/jobs (not Avature), and robots.txt explicitly allows this
    path. The detail endpoint supplies the full description, location, and the
    native SuccessFactors apply URL.
    """

    source = "capgemini"
    company = "Capgemini"
    profile = "desktop"
    list_url = LIST_URL
    country_code: str | None = None
    default_max_pages = 220

    async def scrape(self, ctx: ScrapeContext) -> ScrapeResult:
        result = ScrapeResult()
        page = 1
        total_pages: int | None = None

        async with self.new_client() as http:
            while page <= self.max_pages(ctx):
                if self.limit_reached(ctx, result.seen_ids):
                    result.complete = False
                    result.note("Max jobs per source reached")
                    break

                url = self._list_url(page)
                try:
                    response = await http.get(url, expect_json=True)
                except FetchError as exc:
                    if page == 1:
                        raise
                    result.complete = False
                    result.note(f"List page {page} failed: {exc}")
                    self.log.warning("Capgemini list page %d failed: %s", page, exc)
                    break

                payload = response.json()
                if not isinstance(payload, dict):
                    result.complete = False
                    result.note("Unexpected list payload")
                    break

                items = payload.get("items") or []
                if not items:
                    break
                if total_pages is None:
                    try:
                        total_pages = int(payload.get("total_pages") or 0)
                    except (TypeError, ValueError):
                        total_pages = 0
                    if not total_pages:
                        count = int(payload.get("count") or 0)
                        total_pages = math.ceil(count / PAGE_SIZE) if count else 0
                    self.log.info(
                        "Capgemini: %s jobs across %s pages", payload.get("count"), total_pages
                    )

                for item in items:
                    if self.limit_reached(ctx, result.seen_ids):
                        result.complete = False
                        result.note("Max jobs per source reached")
                        break
                    external_id = str(item.get("id") or "").strip()
                    if not external_id or external_id in result.seen_ids:
                        continue
                    title = clean_text(item.get("title"), max_length=500) or "Untitled role"
                    location = clean_text(item.get("location"), max_length=1000)
                    result.seen_ids.add(external_id)

                    if not self.needs_detail(
                        ctx,
                        external_id,
                        title=title,
                        location=location,
                        compare_location=True,
                    ):
                        continue

                    try:
                        job = await self._fetch_detail(http, item)
                    except (FetchError, ValueError, KeyError) as exc:
                        result.errors += 1
                        result.complete = False
                        self.log.warning("Capgemini detail failed for %s: %s", external_id, exc)
                        continue

                    result.details_fetched += 1
                    result.jobs.append(job)
                    if result.details_fetched % 100 == 0:
                        self.log.info("Capgemini: %d details fetched", result.details_fetched)

                if self.limit_reached(ctx, result.seen_ids):
                    break
                if total_pages and page >= total_pages:
                    break
                page += 1
            else:
                result.complete = False
                result.note("Page limit reached")

        self.log.info(
            "Capgemini: found=%d, details_fetched=%d, errors=%d, complete=%s",
            len(result.seen_ids),
            result.details_fetched,
            result.errors,
            result.complete,
        )
        return result

    def _list_url(self, page: int) -> str:
        url = f"{self.list_url}?page={page}&size={PAGE_SIZE}"
        if self.country_code:
            url += f"&country_code={quote(self.country_code, safe='')}"
        return url

    def _detail_url(self, external_id: str) -> str:
        return f"{self.list_url}/{quote(external_id, safe='+')}"

    async def _fetch_detail(self, http: HttpClient, item: dict[str, Any]) -> JobRecord:
        external_id = str(item["id"])
        url = self._detail_url(external_id)
        response = await http.get(url, expect_json=True)
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("unexpected detail payload")

        title = clean_text(payload.get("title"), max_length=500) or clean_text(
            item.get("title"), max_length=500
        )
        location = clean_text(payload.get("location"), max_length=1000) or clean_text(
            item.get("location"), max_length=1000
        )
        description = self._description_html(payload, title)
        if not description:
            raise ValueError("detail payload has no description")

        posted = parse_date(payload.get("date_published")) or parse_date(payload.get("last_updated"))
        apply_url = safe_http_url(
            payload.get("apply_url"),
            safe_http_url(payload.get("canonical_url"), str(item.get("html_url") or "")),
        )
        job_code = self._job_code(apply_url, external_id)

        return JobRecord(
            source=self.source,
            company=self.company,
            external_id=external_id,
            job_code=job_code,
            title=title or "Untitled role",
            location=location,
            description_html=description,
            apply_url=apply_url,
            posted_date=posted,
        )

    @staticmethod
    def _job_code(apply_url: str, external_id: str) -> str:
        match = re.search(r"/job/[^/]+/(\d+)", apply_url or "")
        return match.group(1) if match else external_id

    @staticmethod
    def _description_html(payload: dict[str, Any], title: str) -> str:
        sections = payload.get("sections") or []
        rendered: list[str] = []
        for section in sections:
            if not isinstance(section, dict):
                continue
            heading = clean_text(section.get("heading"), max_length=300)
            content = str(section.get("content") or "").strip()
            if not content:
                continue
            if heading and heading != title:
                rendered.append(f"<h3>{html_module.escape(heading)}</h3>")
            rendered.append(text_to_html(content))

        if rendered:
            return "\n".join(rendered)

        content = str(payload.get("content") or "").strip()
        if content:
            return text_to_html(content)
        summary = str(payload.get("summary") or "").strip()
        return text_to_html(summary)


class CapgeminiIndiaScraper(CapgeminiScraper):
    """Capgemini India — same public JobStream API with a country filter."""

    source = "capgemini_india"
    company = "Capgemini India"
    country_code = "in-en"
    default_max_pages = 30
