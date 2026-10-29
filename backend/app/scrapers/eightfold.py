from __future__ import annotations

from typing import Any

from bs4 import BeautifulSoup

from app.scrapers.base import FetchError, HttpClient, HttpResponse
from app.scrapers.models import BaseScraper, JobRecord, ScrapeContext, ScrapeResult
from app.utils import clean_text, epoch_to_date, safe_http_url


class EightfoldScraper(BaseScraper):
    """Eightfold career-site API scraper.

    HSBC uses the standard /api/apply/v2/jobs endpoints. Microsoft's site uses
    the PCSX variant (/api/pcsx/search and /api/pcsx/position_details) and
    requires a CSRF token and cookies from its public careers page.
    """

    base_url = ""
    domain = ""
    location = "India"
    page_size = 10
    default_max_pages = 30
    requires_csrf = False

    def __init__(self) -> None:
        super().__init__()
        self._csrf = ""

    async def scrape(self, ctx: ScrapeContext) -> ScrapeResult:
        result = ScrapeResult()
        start = 0
        page_number = 1

        async with self.new_client() as http:
            await self._prepare(http)

            while page_number <= self.max_pages(ctx):
                if self.limit_reached(ctx, result.seen_ids):
                    result.complete = False
                    result.note("Max jobs per source reached")
                    break

                try:
                    payload = await self._fetch_list(http, start)
                except (FetchError, ValueError, KeyError) as exc:
                    if page_number == 1:
                        raise
                    result.complete = False
                    result.note(f"List page {page_number} failed: {exc}")
                    self.log.warning(
                        "%s list page %d failed: %s", self.company, page_number, exc
                    )
                    break

                positions, total = self._parse_list(payload)
                if not positions:
                    break
                if page_number == 1 and total:
                    self.log.info("%s: %d live positions reported", self.company, total)

                for position in positions:
                    if self.limit_reached(ctx, result.seen_ids):
                        result.complete = False
                        result.note("Max jobs per source reached")
                        break
                    external_id = str(position.get("id") or "").strip()
                    if not external_id or external_id in result.seen_ids:
                        continue
                    title = clean_text(
                        position.get("name") or position.get("posting_name"),
                        max_length=500,
                    ) or "Untitled role"
                    location = self._position_location(position)
                    posted = epoch_to_date(
                        position.get("postedTs") or position.get("t_create")
                    )
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
                        job = await self._fetch_detail(http, position)
                    except (FetchError, ValueError, KeyError) as exc:
                        result.errors += 1
                        result.complete = False
                        self.log.warning(
                            "%s detail failed for %s: %s",
                            self.company,
                            external_id,
                            exc,
                        )
                        continue

                    result.details_fetched += 1
                    result.jobs.append(job)
                    if result.details_fetched % 100 == 0:
                        self.log.info(
                            "%s: %d details fetched", self.company, result.details_fetched
                        )

                start += len(positions)
                if total and start >= total:
                    break
                if len(positions) < self.page_size:
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

    async def _prepare(self, http: HttpClient) -> None:
        if not self.requires_csrf:
            return
        page_url = f"{self.base_url}/careers?hl=en&location={self.location}"
        response = await http.get(page_url, fallback_html=True)
        soup = BeautifulSoup(response.text, "html.parser")
        meta = soup.find("meta", attrs={"name": "_csrf"})
        token = (meta.get("content") if meta else "") or ""
        if not token:
            raise ValueError("careers page did not provide a CSRF token")
        self._csrf = str(token)

    def _api_headers(self) -> dict[str, str]:
        return {}

    async def _fetch_list(self, http: HttpClient, start: int) -> dict[str, Any]:
        response = await http.get(
            self._list_url(start),
            expect_json=True,
            headers=self._api_headers(),
        )
        self._update_csrf(response)
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("unexpected list payload")
        return payload

    async def _fetch_detail(
        self, http: HttpClient, position: dict[str, Any]
    ) -> JobRecord:
        response = await http.get(
            self._detail_url(position),
            expect_json=True,
            headers=self._api_headers(),
        )
        self._update_csrf(response)
        payload = response.json()
        return self._parse_detail(payload, position)

    def _update_csrf(self, response: HttpResponse) -> None:
        if not self.requires_csrf:
            return
        for name, value in response.headers.items():
            if name.lower() == "x-csrf-token" and value:
                self._csrf = value
                return

    def _parse_list(self, payload: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
        data = payload.get("data")
        source = data if isinstance(data, dict) else payload
        positions = source.get("positions") or []
        try:
            total = int(source.get("count") or 0)
        except (TypeError, ValueError):
            total = 0
        return positions, total

    @staticmethod
    def _position_location(position: dict[str, Any]) -> str:
        location = clean_text(position.get("location"), max_length=1000)
        if location:
            return location
        names = position.get("locations") or []
        if isinstance(names, list):
            return "; ".join(
                name for name in (clean_text(item, max_length=400) for item in names) if name
            )
        return ""

    def _list_url(self, start: int) -> str:
        raise NotImplementedError

    def _detail_url(self, position: dict[str, Any]) -> str:
        raise NotImplementedError

    def _parse_detail(
        self, payload: dict[str, Any], position: dict[str, Any]
    ) -> JobRecord:
        raise NotImplementedError


class HSBCScraper(EightfoldScraper):
    source = "hsbc"
    company = "HSBC"
    base_url = "https://portal.careers.hsbc.com"
    domain = "hsbc.com"
    default_max_pages = 30

    def _list_url(self, start: int) -> str:
        return (
            f"{self.base_url}/api/apply/v2/jobs?domain={self.domain}"
            f"&start={start}&num={self.page_size}&location={self.location}"
        )

    def _detail_url(self, position: dict[str, Any]) -> str:
        return (
            f"{self.base_url}/api/apply/v2/jobs/{position['id']}"
            f"?domain={self.domain}"
        )

    def _parse_detail(
        self, payload: dict[str, Any], position: dict[str, Any]
    ) -> JobRecord:
        if not isinstance(payload, dict):
            raise ValueError("unexpected detail payload")
        description = (
            payload.get("job_description")
            or payload.get("custom_JD")
            or payload.get("description")
            or ""
        )
        if not description:
            raise ValueError("detail payload has no description")
        external_id = str(payload.get("id") or position.get("id") or "")
        if not external_id:
            raise ValueError("detail payload has no id")
        title = clean_text(payload.get("name"), max_length=500) or clean_text(
            position.get("name"), max_length=500
        )
        location = clean_text(payload.get("location"), max_length=1000) or (
            self._position_location(position)
        )
        posted = epoch_to_date(payload.get("t_create") or position.get("t_create"))
        apply_url = safe_http_url(
            payload.get("canonicalPositionUrl"),
            f"{self.base_url}/careers/job/{external_id}",
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


class MicrosoftScraper(EightfoldScraper):
    source = "microsoft"
    company = "Microsoft"
    base_url = "https://apply.careers.microsoft.com"
    domain = "microsoft.com"
    requires_csrf = True
    default_max_pages = 30

    def _api_headers(self) -> dict[str, str]:
        return {
            "x-csrf-token": self._csrf,
            "Referer": f"{self.base_url}/careers?hl=en&location={self.location}",
            "Accept": "application/json",
        }

    def _list_url(self, start: int) -> str:
        return (
            f"{self.base_url}/api/pcsx/search?domain={self.domain}"
            f"&query=&location={self.location}&start={start}&hl=en"
        )

    def _detail_url(self, position: dict[str, Any]) -> str:
        return (
            f"{self.base_url}/api/pcsx/position_details?position_id={position['id']}"
            f"&domain={self.domain}&hl=en"
        )

    def _parse_detail(
        self, payload: dict[str, Any], position: dict[str, Any]
    ) -> JobRecord:
        if not isinstance(payload, dict):
            raise ValueError("unexpected detail payload")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise ValueError("detail payload has no data object")
        description = data.get("jobDescription") or ""
        if not description:
            raise ValueError("detail payload has no description")
        external_id = str(data.get("id") or position.get("id") or "")
        if not external_id:
            raise ValueError("detail payload has no id")
        title = clean_text(data.get("name"), max_length=500) or clean_text(
            position.get("name"), max_length=500
        )
        location = clean_text(data.get("location"), max_length=1000) or (
            self._position_location(position)
        )
        posted = epoch_to_date(data.get("postedTs") or position.get("postedTs"))
        apply_url = safe_http_url(
            data.get("publicUrl"),
            f"{self.base_url}/careers/job/{external_id}",
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
