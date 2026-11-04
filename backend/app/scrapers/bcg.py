from __future__ import annotations

import json
from typing import Any

from bs4 import BeautifulSoup

from app.scrapers.base import FetchError, HttpClient
from app.scrapers.models import BaseScraper, JobRecord, ScrapeContext, ScrapeResult
from app.utils import clean_text, parse_date, safe_http_url

BCG_SEARCH_URL = "https://careers.bcg.com/global/en/search-results"
BCG_JOB_URL = "https://careers.bcg.com/global/en/job/{job_id}"
BCG_DDO_MARKER = "phApp.ddo ="


def extract_json_object(text: str, marker: str) -> dict[str, Any] | None:
    """Return the balanced JSON object that starts after ``marker``.

    Phenom embeds its first page of results as ``phApp.ddo = {...};`` in the
    page HTML. Regular expressions cannot match the nested braces reliably, so
    walk the text while tracking string/escape state.
    """
    index = text.find(marker)
    if index < 0:
        return None
    start = text.find("{", index)
    if start < 0:
        return None

    depth = 0
    in_string = False
    escaped = False
    for position in range(start, len(text)):
        char = text[position]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    payload = json.loads(text[start : position + 1])
                except json.JSONDecodeError:
                    return None
                return payload if isinstance(payload, dict) else None
    return None


def find_job_posting(html: str) -> dict[str, Any]:
    """Extract the schema.org JobPosting object from a BCG detail page."""
    soup = BeautifulSoup(html, "html.parser")
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.string or script.get_text() or ""
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        candidates = payload if isinstance(payload, list) else [payload]
        for candidate in candidates:
            if isinstance(candidate, dict) and candidate.get("@type") == "JobPosting":
                return candidate
    return {}


class BCGScraper(BaseScraper):
    """Boston Consulting Group — Phenom CareerConnect search results.

    The search page embeds the current page of results as ``phApp.ddo``.
    Pagination uses the ``from`` query parameter (10 hits per page). Detail
    pages carry a complete schema.org JobPosting block, so no private API or
    apply-flow login is needed.
    """

    source = "bcg"
    company = "Boston Consulting Group"
    page_size = 10
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

                try:
                    listings, page_total = await self._fetch_search(http, offset)
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
                    self.log.info("%s: %d keyword matches reported", self.company, total)
                if not listings:
                    break

                for listing in listings:
                    if self.limit_reached(ctx, result.seen_ids):
                        result.complete = False
                        result.note("Max jobs per source reached")
                        break

                    # The keyword search also returns non-India roles that merely
                    # mention India; keep only postings whose country is India.
                    if str(listing.get("country") or "").strip().lower() != "india":
                        continue

                    external_id = str(listing.get("jobId") or "").strip()
                    if not external_id or external_id in result.seen_ids:
                        continue

                    title = clean_text(listing.get("title"), max_length=500) or "Untitled role"
                    location = clean_text(listing.get("location"), max_length=1000) or "India"
                    posted = parse_date(listing.get("postedDate"))
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
                        job = await self._fetch_detail(http, listing)
                    except (FetchError, ValueError, KeyError) as exc:
                        result.errors += 1
                        result.complete = False
                        self.log.warning(
                            "%s detail failed for %s: %s", self.company, external_id, exc
                        )
                        continue

                    result.details_fetched += 1
                    result.jobs.append(job)
                    if result.details_fetched % 50 == 0:
                        self.log.info(
                            "%s: %d details fetched", self.company, result.details_fetched
                        )

                offset += len(listings)
                if total and offset >= total:
                    break
                if len(listings) < self.page_size:
                    break
                page_number += 1
            else:
                result.complete = False
                result.note("Page limit reached")

        self.log.info(
            "%s: found=%d, details_fetched=%d, errors=%d, complete=%s",
            self.company,
            len(result.seen_ids),
            result.details_fetched,
            result.errors,
            result.complete,
        )
        return result

    async def _fetch_search(
        self, http: HttpClient, offset: int
    ) -> tuple[list[dict[str, Any]], int]:
        url = f"{BCG_SEARCH_URL}?keywords=India&from={offset}"
        response = await http.get(url)
        return self._parse_search(response.text)

    @staticmethod
    def _parse_search(page_html: str) -> tuple[list[dict[str, Any]], int]:
        ddo = extract_json_object(page_html, BCG_DDO_MARKER)
        if not ddo:
            raise ValueError("search page did not contain phApp.ddo")
        block = ddo.get("eagerLoadRefineSearch") or {}
        data = block.get("data") if isinstance(block, dict) else None
        listings = (data or {}).get("jobs") if isinstance(data, dict) else None
        try:
            total = int(block.get("totalHits") or 0) if isinstance(block, dict) else 0
        except (TypeError, ValueError):
            total = 0
        return listings if isinstance(listings, list) else [], total

    async def _fetch_detail(
        self, http: HttpClient, listing: dict[str, Any]
    ) -> JobRecord:
        listing_id = str(listing.get("jobId") or "").strip()
        url = BCG_JOB_URL.format(job_id=listing_id)
        response = await http.get(url)
        return self._parse_detail(response.text, listing, url)

    def _parse_detail(
        self, html: str, listing: dict[str, Any], url: str = ""
    ) -> JobRecord:
        listing_id = str(listing.get("jobId") or "").strip()
        apply_url = url or BCG_JOB_URL.format(job_id=listing_id)
        posting = find_job_posting(html)
        if not posting:
            raise ValueError("detail page has no JobPosting JSON-LD")

        description = str(posting.get("description") or "").strip()
        if not description:
            raise ValueError("JobPosting has no description")

        identifier = posting.get("identifier")
        job_code = ""
        if isinstance(identifier, dict):
            job_code = clean_text(identifier.get("value"), max_length=80)
        external_id = job_code or listing_id

        title = clean_text(posting.get("title"), max_length=500) or clean_text(
            listing.get("title"), max_length=500
        )
        location = self._location(posting) or clean_text(
            listing.get("location"), max_length=1000
        )
        posted = parse_date(listing.get("postedDate")) or parse_date(
            posting.get("datePosted")
        )

        return JobRecord(
            source=self.source,
            company=self.company,
            external_id=external_id,
            job_code=job_code or external_id,
            title=title or "Untitled role",
            location=location or "India",
            description_html=description,
            apply_url=safe_http_url(apply_url, BCG_JOB_URL.format(job_id=external_id)),
            posted_date=posted,
        )

    @staticmethod
    def _location(posting: dict[str, Any]) -> str:
        place = posting.get("jobLocation")
        if isinstance(place, list):
            place = place[0] if place else None
        address = place.get("address") if isinstance(place, dict) else None
        if not isinstance(address, dict):
            return ""
        parts = [
            clean_text(address.get("addressLocality"), max_length=200),
            clean_text(address.get("addressRegion"), max_length=200),
            clean_text(address.get("addressCountry"), max_length=200),
        ]
        return ", ".join(part for part in parts if part)
