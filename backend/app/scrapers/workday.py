from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.scrapers.base import FetchError, HttpClient
from app.scrapers.models import BaseScraper, JobRecord, ScrapeContext, ScrapeResult
from app.utils import clean_text, parse_date, safe_http_url

INDIA_LOCATION_KEYWORDS = (
    "ahmedabad",
    "airoli",
    "bangalore",
    "bengaluru",
    "bhopal",
    "bhubaneswar",
    "chandigarh",
    "chennai",
    "coimbatore",
    "delhi",
    "gurgaon",
    "gurugram",
    "hyderabad",
    "indore",
    "jaipur",
    "kochi",
    "kolkata",
    "mumbai",
    "mysore",
    "mysuru",
    "noida",
    "pune",
    "salarpuria",
    "sucheta",
    "thiruvananthapuram",
    "trivandrum",
    "vadodara",
    "vijayawada",
)


@dataclass(slots=True)
class WorkdayListing:
    external_id: str
    title: str
    location: str
    path: str


class WorkdayScraper(BaseScraper):
    """Workday CXS public job API.

    Workday's list endpoint is a POST with location facets. The detail endpoint
    supplies the full HTML description. The India facet ids are discovered from
    the public facet metadata rather than hard-coded, so city changes do not
    break the scraper.
    """

    base_url = ""
    tenant = ""
    site = ""
    company_name = ""
    location_keywords = INDIA_LOCATION_KEYWORDS
    page_size = 20
    default_max_pages = 40

    async def scrape(self, ctx: ScrapeContext) -> ScrapeResult:
        result = ScrapeResult()
        offset = 0
        page_number = 1
        total: int | None = None

        async with self.new_client() as http:
            facet_ids = await self._fetch_india_facets(http)
            if not facet_ids:
                result.note("India location facet unavailable; falling back to text search")
                self.log.warning("%s: India location facet unavailable", self.company)

            while page_number <= self.max_pages(ctx):
                if self.limit_reached(ctx, result.seen_ids):
                    result.complete = False
                    result.note("Max jobs per source reached")
                    break

                try:
                    payload = await self._fetch_page(http, offset, facet_ids)
                except FetchError as exc:
                    if page_number == 1:
                        raise
                    result.complete = False
                    result.note(f"List page {page_number} failed: {exc}")
                    break

                postings = payload.get("jobPostings") or []
                if total is None:
                    try:
                        total = int(payload.get("total") or 0)
                    except (TypeError, ValueError):
                        total = 0
                    self.log.info("%s: %d India roles reported", self.company, total)
                if not postings:
                    break

                for posting in postings:
                    if self.limit_reached(ctx, result.seen_ids):
                        result.complete = False
                        result.note("Max jobs per source reached")
                        break
                    listing = self._parse_listing(posting)
                    if listing is None or listing.external_id in result.seen_ids:
                        continue
                    result.seen_ids.add(listing.external_id)

                    if not self.needs_detail(
                        ctx,
                        listing.external_id,
                        title=listing.title,
                        location=listing.location,
                    ):
                        continue

                    try:
                        job = await self._fetch_detail(http, listing)
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
                            "%s: %d details fetched", self.company, result.details_fetched
                        )

                offset += len(postings)
                if total and offset >= total:
                    break
                if len(postings) < self.page_size:
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

    def _jobs_url(self) -> str:
        return f"{self.base_url}/wday/cxs/{self.tenant}/{self.site}/jobs"

    def _detail_url(self, path: str) -> str:
        return f"{self.base_url}/wday/cxs/{self.tenant}/{self.site}{path}"

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Referer": f"{self.base_url}/{self.site}",
        }

    async def _fetch_page(
        self, http: HttpClient, offset: int, facet_ids: list[str]
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "appliedFacets": {"locations": facet_ids} if facet_ids else {},
            "limit": self.page_size,
            "offset": offset,
            "searchText": "" if facet_ids else "India",
        }
        response = await http.post(
            self._jobs_url(), json=body, expect_json=True, headers=self._headers()
        )
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("unexpected Workday list payload")
        return payload

    async def _fetch_india_facets(self, http: HttpClient) -> list[str]:
        body = {"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""}
        try:
            response = await http.post(
                self._jobs_url(), json=body, expect_json=True, headers=self._headers()
            )
            payload = response.json()
        except (FetchError, ValueError, KeyError, TypeError) as exc:
            self.log.warning("%s: could not load Workday facets: %s", self.company, exc)
            return []
        if not isinstance(payload, dict):
            return []

        ids: list[str] = []
        for facet in payload.get("facets") or []:
            if facet.get("facetParameter") != "locationMainGroup":
                continue
            for group in facet.get("values") or []:
                for value in group.get("values") or []:
                    descriptor = str(value.get("descriptor") or "").lower()
                    if any(keyword in descriptor for keyword in self.location_keywords):
                        facet_id = value.get("id")
                        if facet_id and facet_id not in ids:
                            ids.append(str(facet_id))
        return ids

    def _parse_listing(self, posting: dict[str, Any]) -> WorkdayListing | None:
        path = str(posting.get("externalPath") or "").strip()
        title = clean_text(posting.get("title"), max_length=500)
        if not path or not title:
            return None
        external_id = self._external_id(posting, path)
        location = clean_text(posting.get("locationsText"), max_length=1000)
        return WorkdayListing(
            external_id=external_id,
            title=title,
            location=location,
            path=path,
        )

    @staticmethod
    def _external_id(posting: dict[str, Any], path: str) -> str:
        for field in posting.get("bulletFields") or []:
            text = clean_text(field, max_length=80)
            if text:
                return text
        return path.rstrip("/").rsplit("/", 1)[-1]

    async def _fetch_detail(
        self, http: HttpClient, listing: WorkdayListing
    ) -> JobRecord:
        response = await http.get(
            self._detail_url(listing.path),
            expect_json=True,
            headers={"Accept": "application/json", "Referer": self._headers()["Referer"]},
        )
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("unexpected Workday detail payload")
        info = payload.get("jobPostingInfo")
        if not isinstance(info, dict):
            raise ValueError("Workday detail payload has no jobPostingInfo")

        description = str(info.get("jobDescription") or "").strip()
        if not description:
            raise ValueError("Workday detail payload has no description")

        external_id = clean_text(info.get("jobReqId"), max_length=80) or listing.external_id
        title = clean_text(info.get("title"), max_length=500) or listing.title
        location = clean_text(info.get("location"), max_length=1000) or listing.location
        posted = parse_date(info.get("startDate"))
        apply_url = safe_http_url(
            info.get("externalUrl"),
            f"{self.base_url}/{self.site}{listing.path}",
        )

        return JobRecord(
            source=self.source,
            company=self.company,
            external_id=external_id,
            title=title or "Untitled role",
            location=location,
            description_html=description,
            apply_url=apply_url,
            posted_date=posted,
        )


class PwCIndiaScraper(WorkdayScraper):
    """PwC India — Workday Global Experienced Careers, India location facet."""

    source = "pwc_india"
    company = "PwC India"
    base_url = "https://pwc.wd3.myworkdayjobs.com"
    tenant = "pwc"
    site = "Global_Experienced_Careers"
    default_max_pages = 40
