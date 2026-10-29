from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from urllib.parse import urlencode, urljoin

from bs4 import BeautifulSoup

from app.scrapers.base import FetchError, HttpClient
from app.scrapers.models import BaseScraper, JobRecord, ScrapeContext, ScrapeResult
from app.utils import clean_text, safe_http_url

JOB_LINK_RE = re.compile(r"/job/([^/]+)/(\d+)/?$")
LOCATION_RE = re.compile(r"Location\s*:\s*([^•\n\r]+)", re.IGNORECASE)
DATE_RE = re.compile(
    r"Date\s*:\s*((?:\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4})|(?:[A-Za-z]{3,9}\s+\d{1,2},\s*\d{4}))",
    re.IGNORECASE,
)
LOCATION_STOP_RE = re.compile(r"\s+(?:Other locations|Salary|Date)\s*:", re.IGNORECASE)
CLOSED_RE = re.compile(
    r"position (?:has been|is) (?:filled|closed)|no longer (?:available|accepting)|"
    r"job (?:has )?expired",
    re.IGNORECASE,
)


class ClosedJob(ValueError):
    pass


@dataclass(slots=True)
class Listing:
    external_id: str
    title: str
    url: str


class SuccessFactorsScraper(BaseScraper):
    """SAP SuccessFactors Career Site Builder search + job detail pages.

    Deloitte India and EY expose 25 public job cards per search page and a
    job detail page per requisition. The detail page supplies the full
    description in `.jobdescription`, with `.job` as a metadata/fallback block.
    """

    search_url = ""
    location = "India"
    extra_params: dict[str, str] = {}
    page_size = 25
    default_max_pages = 200

    async def scrape(self, ctx: ScrapeContext) -> ScrapeResult:
        result = ScrapeResult()
        startrow = 0
        page_number = 1

        async with self.new_client() as http:
            while page_number <= self.max_pages(ctx):
                if self.limit_reached(ctx, result.seen_ids):
                    result.complete = False
                    result.note("Max jobs per source reached")
                    break

                params = {
                    "q": "",
                    "locationsearch": self.location,
                    "startrow": startrow,
                    **self.extra_params,
                }
                url = f"{self.search_url}?{urlencode(params)}"
                try:
                    response = await http.get(url, fallback_html=True)
                except FetchError as exc:
                    if page_number == 1:
                        raise
                    result.complete = False
                    result.note(f"Search page {page_number} failed: {exc}")
                    self.log.warning("%s search page %d failed: %s", self.company, page_number, exc)
                    break

                listings = self._parse_listings(response.text, response.url)
                if not listings:
                    self.log.info(
                        "%s: no listings at startrow %d; search complete",
                        self.company,
                        startrow,
                    )
                    break

                new_listings = [
                    item for item in listings if item.external_id not in result.seen_ids
                ]
                if not new_listings:
                    self.log.info("%s: pagination repeated known IDs; stopping", self.company)
                    break

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
                    ):
                        continue

                    try:
                        job = await self._fetch_detail(http, listing)
                    except ClosedJob:
                        # Stale search cards can point at filled requisitions.
                        # Remove them from seen so a complete run deactivates
                        # an older stored copy.
                        result.seen_ids.discard(listing.external_id)
                        self.log.info(
                            "%s: skipping closed job %s",
                            self.company,
                            listing.external_id,
                        )
                        continue
                    except (FetchError, ValueError, KeyError) as exc:
                        result.errors += 1
                        result.complete = False
                        self.log.warning(
                            "%s detail failed for %s: %s",
                            self.company,
                            listing.external_id,
                            exc,
                        )
                        continue

                    result.details_fetched += 1
                    result.jobs.append(job)
                    if result.details_fetched % 50 == 0:
                        self.log.info(
                            "%s: %d details fetched (startrow %d)",
                            self.company,
                            result.details_fetched,
                            startrow,
                        )

                if self.limit_reached(ctx, result.seen_ids):
                    break
                startrow += self.page_size
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

    def _parse_listings(self, html: str, base_url: str) -> list[Listing]:
        soup = BeautifulSoup(html, "html.parser")
        anchors = soup.select("a.jobTitle-link")
        if not anchors:
            anchors = soup.find_all("a", href=True)

        listings: list[Listing] = []
        seen: set[str] = set()
        for anchor in anchors:
            href = safe_http_url(urljoin(base_url, anchor.get("href", "")), "")
            if not href:
                continue
            match = JOB_LINK_RE.search(href)
            if not match:
                continue
            external_id = match.group(2)
            if external_id in seen:
                continue
            title = clean_text(anchor.get_text(" ", strip=True), max_length=500)
            if not title:
                continue
            listings.append(Listing(external_id=external_id, title=title, url=href))
            seen.add(external_id)
        return listings

    async def _fetch_detail(self, http: HttpClient, listing: Listing) -> JobRecord:
        response = await http.get(listing.url, fallback_html=True)
        soup = BeautifulSoup(response.text, "html.parser")
        node = (
            soup.select_one(".jobdescription")
            or soup.select_one(".job")
            or soup.select_one(".jobDisplay")
            or soup.select_one("main")
        )
        if node is None:
            raise ValueError("detail page has no description container")

        description_html = str(node)
        text = node.get_text(" ", strip=True)
        if not text:
            raise ValueError("detail page has an empty description")
        if self._is_closed(text):
            raise ClosedJob("position is filled or closed")

        # EY's `.jobdescription` contains only the description; location and
        # date live in the surrounding `.job` / `.jobDisplay` metadata block.
        meta_node = (
            soup.select_one(".job")
            or soup.select_one(".jobDisplay")
            or soup.select_one(".jobDisplayShell")
            or node
        )
        meta_text = meta_node.get_text(" ", strip=True)

        title = self._detail_title(soup) or listing.title
        location = self._detail_location(meta_text)
        posted = self._detail_date(meta_text)

        return JobRecord(
            source=self.source,
            company=self.company,
            external_id=listing.external_id,
            title=title or "Untitled role",
            location=location,
            description_html=description_html,
            apply_url=listing.url,
            posted_date=posted,
        )

    @staticmethod
    def _is_closed(text: str) -> bool:
        return bool(CLOSED_RE.search(text))

    @staticmethod
    def _detail_title(soup: BeautifulSoup) -> str:
        heading = soup.find("h1")
        title = clean_text(heading.get_text(" ", strip=True), max_length=500) if heading else ""
        return re.sub(r"^Job Title\s*:\s*", "", title, flags=re.IGNORECASE)

    @staticmethod
    def _detail_location(text: str) -> str:
        match = LOCATION_RE.search(text)
        if not match:
            return ""
        value = LOCATION_STOP_RE.split(match.group(1), maxsplit=1)[0]
        return clean_text(value, max_length=1000)

    @staticmethod
    def _detail_date(text: str) -> date | None:
        match = DATE_RE.search(text)
        if not match:
            return None
        raw = match.group(1)
        for fmt in ("%d %b %Y", "%d %B %Y", "%b %d, %Y", "%B %d, %Y"):
            try:
                return datetime.strptime(raw, fmt).date()
            except ValueError:
                continue
        return None


class DeloitteIndiaScraper(SuccessFactorsScraper):
    source = "deloitte_india"
    company = "Deloitte India"
    search_url = "https://southasiacareers.deloitte.com/search/"
    default_max_pages = 120


class EYScraper(SuccessFactorsScraper):
    source = "ey"
    company = "EY"
    search_url = "https://careers.ey.com/ey/search/"
    extra_params = {"local": "en_US"}
    default_max_pages = 200
