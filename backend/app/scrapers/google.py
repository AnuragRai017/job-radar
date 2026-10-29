from __future__ import annotations

import json
import math
import re
from datetime import date
from typing import Any

from app.config import settings
from app.scrapers.base import FetchError
from app.scrapers.models import BaseScraper, JobRecord, ScrapeContext, ScrapeResult
from app.utils import clean_text, epoch_to_date

RESULTS_URL = "https://www.google.com/about/careers/applications/jobs/results"
DS1_RE = re.compile(
    r"AF_initDataCallback\(\{key:\s*['\"]ds:1['\"],\s*hash:\s*['\"][^'\"]*['\"],\s*data:\s*"
)


class GoogleScraper(BaseScraper):
    """Google Careers — server-rendered AF_initDataCallback payload.

    The results page embeds complete job records in the ds:1 blob. One request
    yields 20 jobs with title, description, qualifications, locations and date;
    no per-job detail request is needed.

    robots.txt disallows the ?page= query. With strict robots enabled this
    scraper fetches the allowed first page (20 jobs). Set
    GOOGLE_PAGINATION_OVERRIDE=true to explicitly opt into full pagination.
    """

    source = "google"
    company = "Google"
    profile = "desktop"
    default_max_pages = 400

    async def scrape(self, ctx: ScrapeContext) -> ScrapeResult:
        result = ScrapeResult()
        total_pages: int | None = None
        page = 1
        override = settings.google_pagination_override

        async with self.new_client() as http:
            while page <= self.max_pages(ctx):
                if self.limit_reached(ctx, result.seen_ids):
                    result.complete = False
                    result.note("Max jobs per source reached")
                    break

                url = RESULTS_URL if page == 1 else f"{RESULTS_URL}?page={page}"
                bypass_robots = override
                if page > 1 and settings.respect_robots and not override:
                    allowed = await http.can_fetch(url)
                    if not allowed:
                        result.complete = False
                        result.note(
                            "robots.txt disallows ?page= pagination; fetched the first page only"
                        )
                        self.log.warning(
                            "Google pagination blocked by robots.txt; stopping after page 1. "
                            "Set GOOGLE_PAGINATION_OVERRIDE=true only after reviewing the site terms."
                        )
                        break

                try:
                    response = await http.get(url, enforce_robots=not bypass_robots)
                except FetchError as exc:
                    if page == 1:
                        raise
                    result.complete = False
                    result.note(f"Results page {page} failed: {exc}")
                    self.log.warning("Google results page %d failed: %s", page, exc)
                    break

                data = self._extract_ds1(response.text)
                if data is None:
                    self.log.info("Google: no ds:1 payload on page %d; stopping", page)
                    break
                records = data[0] if isinstance(data, list) and data else None
                if not isinstance(records, list) or not records:
                    break

                if total_pages is None:
                    total = data[2] if len(data) > 2 and isinstance(data[2], int) else 0
                    page_size = len(records) or 20
                    total_pages = math.ceil(total / page_size) if total else 0
                    self.log.info(
                        "Google: %s jobs, %s pages (page size %d)", total, total_pages, page_size
                    )

                for record in records:
                    if self.limit_reached(ctx, result.seen_ids):
                        result.complete = False
                        result.note("Max jobs per source reached")
                        break
                    job = self._parse_record(record)
                    if job is None:
                        result.errors += 1
                        continue
                    if job.external_id in result.seen_ids:
                        continue
                    result.seen_ids.add(job.external_id)
                    result.jobs.append(job)

                if self.limit_reached(ctx, result.seen_ids):
                    break
                if page == 1 and settings.respect_robots and not override and total_pages > 1:
                    result.complete = False
                    result.note(
                        "robots.txt disallows ?page= pagination; fetched the first page only"
                    )
                    self.log.warning(
                        "Google: strict robots mode fetched only the first page (%d of %d). "
                        "See README compliance notes.",
                        len(records),
                        total_pages,
                    )
                    break
                if total_pages and page >= total_pages:
                    break
                page += 1
            else:
                result.complete = False
                result.note("Page limit reached")

        self.log.info(
            "Google: found=%d, errors=%d, complete=%s",
            len(result.seen_ids),
            result.errors,
            result.complete,
        )
        return result

    @staticmethod
    def _extract_ds1(html: str) -> Any | None:
        match = DS1_RE.search(html)
        if not match:
            return None
        payload = GoogleScraper._extract_balanced(html, match.end())
        if payload is None:
            return None
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            return None

    @staticmethod
    def _extract_balanced(text: str, start: int) -> str | None:
        if start >= len(text) or text[start] not in "[{":
            return None
        opener = text[start]
        closer = "]" if opener == "[" else "}"
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(text)):
            char = text[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == opener:
                depth += 1
            elif char == closer:
                depth -= 1
                if depth == 0:
                    return text[start : index + 1]
        return None

    def _parse_record(self, record: Any) -> JobRecord | None:
        if not isinstance(record, list) or len(record) < 13:
            return None
        external_id = str(record[0] or "").strip()
        title = clean_text(record[1], max_length=500)
        if not external_id or not title:
            return None

        location = self._locations(record[9])
        posted = self._posted_date(record)

        description_parts: list[str] = []
        about = self._html_field(record, 10)
        responsibilities = self._html_field(record, 3)
        qualifications = self._html_field(record, 4)
        location_note = self._html_field(record, 18)

        if about:
            description_parts.append(f"<h2>About the job</h2>{about}")
        if responsibilities:
            description_parts.append(f"<h2>Responsibilities</h2>{responsibilities}")
        if qualifications:
            description_parts.append(f"<h2>Qualifications</h2>{qualifications}")
        if location_note:
            description_parts.append(f"<p><em>{location_note}</em></p>")

        description = "\n".join(description_parts)
        if not description:
            return None

        # The sign-in-gated apply URL in record[2] is not used; the canonical
        # results page is stable, public, and works as a navigation target.

        return JobRecord(
            source=self.source,
            company=self.company,
            external_id=external_id,
            title=title,
            location=location,
            description_html=description,
            apply_url=f"{RESULTS_URL}/{external_id}",
            posted_date=posted,
        )

    @staticmethod
    def _html_field(record: list[Any], index: int) -> str:
        if index >= len(record):
            return ""
        value = record[index]
        if isinstance(value, list):
            for item in value:
                if isinstance(item, str) and item.strip():
                    return item.strip()
            return ""
        return value.strip() if isinstance(value, str) else ""

    @staticmethod
    def _locations(value: Any) -> str:
        if not isinstance(value, list):
            return ""
        names: list[str] = []
        seen: set[str] = set()
        for entry in value:
            name = ""
            if isinstance(entry, list) and entry:
                if isinstance(entry[0], str) and entry[0].strip():
                    name = entry[0].strip()
                elif len(entry) > 5:
                    parts = [entry[2], entry[4], entry[5]]
                    name = ", ".join(str(part) for part in parts if part)
            elif isinstance(entry, str):
                name = entry.strip()
            if name and name.lower() not in seen:
                seen.add(name.lower())
                names.append(name)
        return "; ".join(names)

    @staticmethod
    def _posted_date(record: list[Any]) -> date | None:
        for index in (12, 13, 14):
            if index < len(record):
                parsed = epoch_to_date(record[index])
                if parsed:
                    return parsed
        return None
