from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from app.scrapers.base import FetchError, HttpClient
from app.scrapers.models import BaseScraper, JobRecord, ScrapeContext, ScrapeResult
from app.utils import clean_text

LIST_URL = "https://www.cadence.care/open-roles/business-and-tech"
LOCATION_RE = re.compile(r"(?:^|\n)\s*Location\s*:\s*([^\n]+)", re.IGNORECASE)


@dataclass(slots=True)
class Listing:
    external_id: str
    title: str
    url: str


class CadenceScraper(BaseScraper):
    """Cadence — public open-roles listing and server-rendered job detail pages."""

    source = "cadence"
    company = "Cadence"
    default_max_pages = 1

    async def scrape(self, ctx: ScrapeContext) -> ScrapeResult:
        result = ScrapeResult()
        async with self.new_client() as http:
            response = await http.get(LIST_URL, fallback_html=True)
            listings = self._parse_listings(response.text, response.url)
            if not listings:
                result.complete = False
                result.note("Cadence returned no open roles")
                return result

            for listing in listings:
                if self.limit_reached(ctx, result.seen_ids):
                    result.complete = False
                    result.note("Max jobs per source reached")
                    break
                if listing.external_id in result.seen_ids:
                    continue
                result.seen_ids.add(listing.external_id)

                if not self.needs_detail(
                    ctx,
                    listing.external_id,
                    title=listing.title,
                ):
                    continue

                try:
                    job = await self._fetch_detail(http, listing)
                except (FetchError, ValueError, KeyError) as exc:
                    result.errors += 1
                    result.complete = False
                    self.log.warning(
                        "Cadence detail failed for %s: %s", listing.external_id, exc
                    )
                    continue

                result.details_fetched += 1
                result.jobs.append(job)

        self.log.info(
            "Cadence: found=%d, details_fetched=%d, errors=%d, complete=%s",
            len(result.seen_ids),
            result.details_fetched,
            result.errors,
            result.complete,
        )
        return result

    @staticmethod
    def _parse_listings(html: str, base_url: str) -> list[Listing]:
        soup = BeautifulSoup(html, "html.parser")
        listings: list[Listing] = []
        seen: set[str] = set()
        for anchor in soup.select('a[href*="/jobs/"]'):
            href = urljoin(base_url, anchor.get("href", ""))
            path = urlparse(href).path.rstrip("/")
            external_id = path.rsplit("/", 1)[-1] if "/jobs/" in path else ""
            title = clean_text(anchor.get_text(" ", strip=True), max_length=500)
            if not external_id or not title or external_id in seen:
                continue
            listings.append(Listing(external_id=external_id, title=title, url=href))
            seen.add(external_id)
        return listings

    async def _fetch_detail(self, http: HttpClient, listing: Listing) -> JobRecord:
        response = await http.get(listing.url, fallback_html=True)
        soup = BeautifulSoup(response.text, "html.parser")
        node = (
            soup.select_one(".job-description__content")
            or soup.select_one(".job-description")
            or soup.select_one("main")
        )
        if node is None:
            raise ValueError("detail page has no description container")
        description = str(node)
        text = node.get_text(" ", strip=True)
        if not text:
            raise ValueError("detail page has an empty description")

        heading = soup.find("h1")
        title = clean_text(heading.get_text(" ", strip=True), max_length=500) if heading else ""
        title = title or listing.title
        location = self._location(text, title)

        return JobRecord(
            source=self.source,
            company=self.company,
            external_id=listing.external_id,
            title=title,
            location=location,
            description_html=description,
            apply_url=listing.url,
            posted_date=None,
        )

    @staticmethod
    def _location(text: str, title: str) -> str:
        match = LOCATION_RE.search(text)
        if match:
            return clean_text(match.group(1), max_length=1000)
        if "remote" in title.lower() or "remote" in text.lower():
            return "Remote"
        return ""
