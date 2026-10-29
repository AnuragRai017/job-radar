from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from app.scrapers.ai_fallback import AiFallback, AiListing
from app.scrapers.base import FetchError, HttpClient
from app.scrapers.models import BaseScraper, JobRecord, ScrapeContext, ScrapeResult
from app.utils import clean_text, parse_date, safe_http_url

SEARCH_URL = "https://apply.deloitte.com/en_US/careers/SearchJobs/"
PORTAL_ROOT = "https://apply.deloitte.com/en_US/careers/"
APPLY_URL = "https://apply.deloitte.com/en_US/careers/Login?jobId={job_id}"
JOB_ID_RE = re.compile(r"/JobDetail/[^/]+/(\d+)")
ALLOWED_HOSTS = {"apply.deloitte.com"}


@dataclass(slots=True)
class Listing:
    external_id: str
    title: str
    location: str
    url: str


class DeloitteScraper(BaseScraper):
    """Deloitte US — Avature portal.

    Avature does not publish a documented public JSON API for this portal. The
    search page is used only to discover job IDs; every job's description,
    title, date, and location come from the embedded schema.org JobPosting
    JSON-LD on the detail page (structured data, not UI scraping).
    """

    source = "deloitte"
    company = "Deloitte"
    profile = "avature"
    default_max_pages = 300

    async def scrape(self, ctx: ScrapeContext) -> ScrapeResult:
        result = ScrapeResult()
        ai = AiFallback()
        ai_listing_mode = False
        ai_listing_used = False
        ai_details_used = 0
        offset = 0
        page_number = 1
        pages_without_new_ids = 0

        if ai.enabled:
            self.log.info(
                "Deloitte: AI fallback enabled (model=%s, max_calls=%d)",
                ai.model,
                ai.max_calls,
            )

        async with self.new_client() as http:
            while page_number <= self.max_pages(ctx):
                if self.limit_reached(ctx, result.seen_ids):
                    result.complete = False
                    result.note("Max jobs per source reached")
                    break

                url = f"{SEARCH_URL}?jobRecordsPerPage=10&jobOffset={offset}"
                try:
                    response = await http.get(url, fallback_html=True)
                except FetchError as exc:
                    if page_number == 1:
                        raise
                    result.complete = False
                    result.note(f"Search page {page_number} failed: {exc}")
                    self.log.warning("Deloitte search page %d failed: %s", page_number, exc)
                    break

                listings = self._parse_listings(response.text, response.url)
                if not listings and (offset == 0 or ai_listing_mode) and ai.enabled:
                    ai_listings = await ai.extract_listings(
                        response.text,
                        base_url=response.url,
                        allowed_hosts=ALLOWED_HOSTS,
                        path_pattern=JOB_ID_RE,
                    )
                    if ai_listings is None:
                        result.complete = False
                        if ai.available:
                            result.note("AI listing fallback failed")
                        else:
                            result.note("AI listing fallback stopped (budget or circuit breaker)")
                    elif ai_listings:
                        listings = self._listings_from_ai(ai_listings)
                        ai_listing_mode = True
                        if not ai_listing_used:
                            ai_listing_used = True
                            result.note("AI fallback used for Deloitte listing discovery")
                        if not listings:
                            result.complete = False
                            result.note("AI listing fallback had no usable job IDs")
                    # An empty list is a valid "end of results" answer.

                if not listings:
                    self.log.info("Deloitte: no results at offset %d; listing complete", offset)
                    break

                new_listings = [item for item in listings if item.external_id not in result.seen_ids]
                if not new_listings:
                    pages_without_new_ids += 1
                    if pages_without_new_ids >= 2:
                        self.log.warning("Deloitte pagination stopped returning new IDs; stopping")
                        result.complete = False
                        result.note("Pagination stopped returning new IDs")
                        break
                else:
                    pages_without_new_ids = 0

                for listing in new_listings:
                    if self.limit_reached(ctx, result.seen_ids):
                        result.complete = False
                        result.note("Max jobs per source reached")
                        break
                    result.seen_ids.add(listing.external_id)

                    if not self.needs_detail(
                        ctx,
                        listing.external_id,
                        title=listing.title,
                        location=listing.location,
                        compare_location=False,
                    ):
                        continue

                    try:
                        job, used_ai = await self._fetch_detail(http, listing, ai=ai)
                    except (FetchError, ValueError, KeyError) as exc:
                        result.errors += 1
                        result.complete = False
                        self.log.warning("Deloitte detail failed for %s: %s", listing.external_id, exc)
                        continue

                    if used_ai:
                        ai_details_used += 1
                    result.details_fetched += 1
                    result.jobs.append(job)
                    if result.details_fetched % 50 == 0:
                        self.log.info(
                            "Deloitte: %d details fetched (offset %d)", result.details_fetched, offset
                        )

                offset += 10
                page_number += 1
            else:
                result.complete = False
                result.note("Page limit reached")

        if ai_details_used:
            result.note(f"AI fallback extracted {ai_details_used} Deloitte detail page(s)")
        if ai.circuit_open:
            result.note("AI fallback circuit opened after repeated failures")
        self.log.info(
            "Deloitte: found=%d, details_fetched=%d, errors=%d, ai_calls=%d, complete=%s",
            len(result.seen_ids),
            result.details_fetched,
            result.errors,
            ai.calls,
            result.complete,
        )
        return result

    def _parse_listings(self, html: str, base_url: str) -> list[Listing]:
        soup = BeautifulSoup(html, "html.parser")
        listings: list[Listing] = []
        seen: set[str] = set()

        for article in soup.select("article.article--result"):
            anchor = article.select_one('a[href*="/JobDetail/"]')
            if anchor is None:
                continue
            href = urljoin(base_url, anchor.get("href", ""))
            match = JOB_ID_RE.search(href)
            if not match:
                continue
            external_id = match.group(1)
            if external_id in seen:
                continue
            title = clean_text(anchor.get_text(" ", strip=True), max_length=500)
            if not title:
                continue
            subtitle_spans = [
                clean_text(span.get_text(" ", strip=True))
                for span in article.select(".article__header__text__subtitle span")
            ]
            location = subtitle_spans[-1] if subtitle_spans else "Multiple Locations"
            if not location:
                location = "Multiple Locations"
            listings.append(
                Listing(
                    external_id=external_id,
                    title=title,
                    location=location,
                    url=href,
                )
            )
            seen.add(external_id)
        return listings

    def _listings_from_ai(self, ai_listings: list[AiListing]) -> list[Listing]:
        listings: list[Listing] = []
        seen: set[str] = set()
        for item in ai_listings:
            href = safe_http_url(item.url, "")
            if not href:
                continue
            match = JOB_ID_RE.search(href)
            if not match:
                continue
            external_id = match.group(1)
            if external_id in seen:
                continue
            title = clean_text(item.title, max_length=500)
            if not title:
                continue
            location = clean_text(item.location, max_length=400) or "Multiple Locations"
            listings.append(
                Listing(
                    external_id=external_id,
                    title=title,
                    location=location,
                    url=href,
                )
            )
            seen.add(external_id)
        return listings

    async def _fetch_detail(
        self,
        http: HttpClient,
        listing: Listing,
        *,
        ai: AiFallback | None = None,
    ) -> tuple[JobRecord, bool]:
        response = await http.get(listing.url, fallback_html=True)
        soup = BeautifulSoup(response.text, "html.parser")
        posting = self._extract_job_posting(soup)
        description = (posting.get("description") or "") if posting else ""

        apply_url = safe_http_url(
            APPLY_URL.format(job_id=listing.external_id),
            f"{PORTAL_ROOT}JobDetail/{listing.external_id}",
        )

        if posting is None or not description:
            ai_job = None
            if ai is not None and ai.enabled:
                ai_job = await ai.extract_job(
                    response.text,
                    page_url=response.url,
                    title_hint=listing.title,
                    location_hint=listing.location,
                )
            if ai_job is None:
                if posting is None:
                    raise ValueError("no JobPosting JSON-LD found")
                raise ValueError("JobPosting JSON-LD has no description")

            return (
                JobRecord(
                    source=self.source,
                    company=self.company,
                    external_id=listing.external_id,
                    title=listing.title or ai_job.title or "Untitled role",
                    location=self._detail_location(soup)
                    or ai_job.location
                    or listing.location,
                    description_html=ai_job.description_html,
                    apply_url=apply_url,
                    posted_date=ai_job.posted_date,
                ),
                True,
            )

        posted = parse_date(posting.get("datePosted"))
        location = self._detail_location(soup) or listing.location
        title = listing.title
        jsonld_title = clean_text(posting.get("title"), max_length=500)
        # Prefer the public listing title; some portals expose an internal
        # requisition title in the first JSON-LD block.
        if not title and jsonld_title:
            title = jsonld_title

        return (
            JobRecord(
                source=self.source,
                company=self.company,
                external_id=listing.external_id,
                title=title or "Untitled role",
                location=location,
                description_html=description,
                apply_url=apply_url,
                posted_date=posted,
            ),
            False,
        )

    @staticmethod
    def _extract_job_posting(soup: BeautifulSoup) -> dict[str, Any] | None:
        candidates: list[dict[str, Any]] = []
        for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
            raw = (script.string or script.get_text() or "").strip()
            if not raw:
                continue
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                continue

            stack = payload if isinstance(payload, list) else [payload]
            for entry in stack:
                if not isinstance(entry, dict):
                    continue
                if "@graph" in entry and isinstance(entry["@graph"], list):
                    stack.extend(entry["@graph"])
                    continue
                entry_type = entry.get("@type")
                types = entry_type if isinstance(entry_type, list) else [entry_type]
                if "JobPosting" in types and entry.get("description"):
                    candidates.append(entry)

        if not candidates:
            return None
        # The richest block contains the full HTML description.
        return max(candidates, key=lambda item: len(str(item.get("description") or "")))

    @staticmethod
    def _detail_location(soup: BeautifulSoup) -> str:
        selectors = (
            ".article__header--locations",
            ".article__header__text__subtitle",
        )
        for selector in selectors:
            node = soup.select_one(selector)
            if node is None:
                continue
            paragraphs = [
                clean_text(paragraph.get_text(" ", strip=True), max_length=400)
                for paragraph in node.select("p")
            ]
            paragraphs = [paragraph for paragraph in paragraphs if paragraph]
            if paragraphs:
                deduped: list[str] = []
                seen: set[str] = set()
                for paragraph in paragraphs:
                    if paragraph.lower() not in seen:
                        seen.add(paragraph.lower())
                        deduped.append(paragraph)
                return "; ".join(deduped)
            text = clean_text(node.get_text(" ", strip=True), max_length=1000)
            if text:
                return text
        return ""
