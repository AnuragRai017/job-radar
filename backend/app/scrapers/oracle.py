from __future__ import annotations

from datetime import date
from typing import Any

from app.scrapers.base import FetchError, HttpClient
from app.scrapers.models import BaseScraper, JobRecord, ScrapeContext, ScrapeResult
from app.utils import clean_text, parse_datetime, safe_http_url


class OracleRecruitingScraper(BaseScraper):
    """Oracle Recruiting Cloud public REST API.

    The list endpoint returns paginated requisition rows; the detail endpoint
    supplies the full description. Both JPMorgan Chase and EXL use the same
    API shape with different hosts, site numbers, and company branding.
    """

    list_url = ""
    detail_url = ""
    site_number = ""
    location_filter = ""
    job_page = ""
    page_size = 200
    oracle_offset_cap = 9_800
    default_max_pages = 60

    async def scrape(self, ctx: ScrapeContext) -> ScrapeResult:
        result = ScrapeResult()
        async with self.new_client() as http:
            first_payload = await self._fetch_page(http, 0)
            if first_payload is None:
                raise FetchError(self.list_url, f"{self.company} list endpoint returned no data")

            requisitions, total, categories = first_payload
            if total <= 0:
                result.complete = False
                result.note(f"{self.company} returned zero jobs")
                return result

            self.log.info("%s: %d live requisitions reported", self.company, total)

            # Oracle exposes at most 10,000 rows per query. Beyond that we walk
            # CATEGORIES facets and paginate each category independently.
            if total > self.oracle_offset_cap:
                result.note(
                    f"Total {total} exceeds Oracle's 9,800 offset cap; using CATEGORIES facets"
                )
                if not categories:
                    categories = await self._fetch_categories(http)
                if not categories:
                    result.complete = False
                    result.note("Oracle facet fallback unavailable; capped at 9,800")
                    await self._consume_requisitions(http, ctx, result, requisitions)
                else:
                    await self._consume_facets(http, ctx, result, categories)
            else:
                await self._consume_requisitions(http, ctx, result, requisitions)
                offset = self.page_size
                page_count = 1
                while offset < total and not self.limit_reached(ctx, result.seen_ids):
                    if ctx.max_pages and page_count >= self.max_pages(ctx):
                        result.complete = False
                        result.note("Page limit reached")
                        break
                    if offset > self.oracle_offset_cap:
                        result.complete = False
                        result.note("Reached Oracle's 9,800 offset cap")
                        break
                    try:
                        payload = await self._fetch_page(http, offset)
                    except FetchError as exc:
                        result.complete = False
                        result.note(f"List page at offset {offset} failed: {exc}")
                        self.log.warning(
                            "%s list page offset %d failed: %s",
                            self.company,
                            offset,
                            exc,
                        )
                        break
                    if payload is None:
                        break
                    page_reqs, _page_total, _page_categories = payload
                    if not page_reqs:
                        break
                    await self._consume_requisitions(http, ctx, result, page_reqs)
                    offset += self.page_size
                    page_count += 1

        if self.limit_reached(ctx, result.seen_ids):
            result.complete = False
            result.note("Max jobs per source reached")

        self.log.info(
            "%s: found=%d, details_fetched=%d, errors=%d, complete=%s",
            self.company,
            len(result.seen_ids),
            result.details_fetched,
            result.errors,
            result.complete,
        )
        return result

    async def _fetch_page(
        self, http: HttpClient, offset: int, category_id: str | int | None = None
    ) -> tuple[list[dict[str, Any]], int, list[dict[str, Any]]] | None:
        category_filter = f",selectedCategoriesFacet={category_id}" if category_id else ""
        url = (
            f"{self.list_url}?finder=findReqs;siteNumber={self.site_number}"
            f"{self.location_filter}{category_filter},"
            f"limit={self.page_size},offset={offset},sortBy=POSTING_DATES_DESC"
            "&expand=requisitionList.secondaryLocations"
        )
        response = await http.get(url, expect_json=True)
        payload = response.json()
        if not isinstance(payload, dict):
            return None
        items = payload.get("items") or []
        if not items:
            return None
        item = items[0]
        try:
            total = int(item.get("TotalJobsCount") or 0)
        except (TypeError, ValueError):
            total = 0
        requisitions = item.get("requisitionList") or []
        categories = item.get("categoriesFacet") or []
        return requisitions, total, categories

    async def _fetch_categories(self, http: HttpClient) -> list[dict[str, Any]]:
        url = (
            f"{self.list_url}?finder=findReqs;siteNumber={self.site_number}"
            f"{self.location_filter},"
            "facetsList=CATEGORIES,limit=1,offset=0&expand=requisitionList.secondaryLocations"
        )
        response = await http.get(url, expect_json=True)
        payload = response.json()
        if not isinstance(payload, dict):
            return []
        items = payload.get("items") or []
        return items[0].get("categoriesFacet") or [] if items else []

    async def _consume_facets(
        self, http: HttpClient, ctx: ScrapeContext, result: ScrapeResult, categories: list[dict[str, Any]]
    ) -> None:
        for category in categories:
            if self.limit_reached(ctx, result.seen_ids):
                result.complete = False
                return
            category_id = category.get("Id")
            if category_id is None:
                continue
            try:
                category_total = int(category.get("TotalCount") or 0)
            except (TypeError, ValueError):
                category_total = 0
            if category_total <= 0:
                continue
            if category_total > self.oracle_offset_cap:
                result.complete = False
                result.note(
                    f"Category {category.get('Name') or category_id} exceeds 9,800; capped"
                )
            offset = 0
            while offset < min(category_total, self.oracle_offset_cap + self.page_size):
                if self.limit_reached(ctx, result.seen_ids):
                    result.complete = False
                    return
                try:
                    payload = await self._fetch_page(http, offset, category_id)
                except FetchError as exc:
                    result.complete = False
                    result.note(f"Category {category_id} offset {offset} failed: {exc}")
                    break
                if payload is None:
                    break
                requisitions, _total, _categories = payload
                if not requisitions:
                    break
                await self._consume_requisitions(http, ctx, result, requisitions)
                offset += self.page_size

    async def _consume_requisitions(
        self,
        http: HttpClient,
        ctx: ScrapeContext,
        result: ScrapeResult,
        requisitions: list[dict[str, Any]],
    ) -> None:
        for item in requisitions:
            if self.limit_reached(ctx, result.seen_ids):
                result.complete = False
                return
            external_id = str(item.get("Id") or "").strip()
            if not external_id or external_id in result.seen_ids:
                continue

            list_title = clean_text(item.get("Title"), max_length=500) or "Untitled role"
            list_location = self._list_location(item)
            list_date = self._date(item.get("PostedDate"))
            result.seen_ids.add(external_id)

            if not self.needs_detail(
                ctx,
                external_id,
                title=list_title,
                location=list_location,
                posted_date=list_date,
            ):
                continue

            try:
                job = await self._fetch_detail(
                    http, external_id, list_title, list_location, list_date
                )
            except (FetchError, ValueError, KeyError) as exc:
                result.errors += 1
                result.complete = False
                self.log.warning(
                    "%s detail failed for %s: %s", self.company, external_id, exc
                )
                continue

            result.details_fetched += 1
            result.jobs.append(job)
            if result.details_fetched % 100 == 0:
                self.log.info("%s: %d details fetched", self.company, result.details_fetched)

    async def _fetch_detail(
        self,
        http: HttpClient,
        external_id: str,
        list_title: str,
        list_location: str,
        list_date: date | None,
    ) -> JobRecord:
        url = (
            f"{self.detail_url}?expand=all&onlyData=true&finder=ById;"
            f'Id=%22{external_id}%22,siteNumber={self.site_number}'
        )
        response = await http.get(url, expect_json=True)
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("unexpected detail payload")
        items = payload.get("items") or []
        if not items:
            raise ValueError("detail endpoint returned no item")
        detail = items[0]

        description = detail.get("ExternalDescriptionStr") or ""
        if not description:
            short = detail.get("ShortDescriptionStr") or ""
            description = f"<p>{short}</p>" if short else ""
        if not description:
            raise ValueError("detail endpoint returned an empty description")

        title = clean_text(detail.get("Title"), max_length=500) or list_title
        location = self._detail_location(detail) or list_location
        posted = self._date(detail.get("ExternalPostedStartDate")) or list_date
        apply_url = safe_http_url(
            detail.get("ExternalURL"), f"{self.job_page}/{external_id}"
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

    @staticmethod
    def _date(value: object) -> date | None:
        parsed = parse_datetime(value)
        return parsed.date() if parsed else None

    @staticmethod
    def _list_location(item: dict[str, Any]) -> str:
        names: list[str] = []
        primary = item.get("PrimaryLocation")
        if primary:
            names.append(str(primary))
        for loc in item.get("secondaryLocations") or []:
            if isinstance(loc, dict):
                name = loc.get("LocationName") or loc.get("Name") or loc.get("Location")
                if name:
                    names.append(str(name))
        return _dedupe_join(names)

    @staticmethod
    def _detail_location(detail: dict[str, Any]) -> str:
        names: list[str] = []
        primary = detail.get("PrimaryLocation")
        if primary:
            names.append(str(primary))
        for key in ("secondaryLocations", "otherWorkLocations"):
            for loc in detail.get(key) or []:
                if isinstance(loc, dict):
                    name = loc.get("LocationName") or loc.get("Name") or loc.get("Location")
                    if name:
                        names.append(str(name))
        return _dedupe_join(names)


def _dedupe_join(values: list[str]) -> str:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        text = clean_text(value, max_length=400)
        if text and text.lower() not in seen:
            seen.add(text.lower())
            output.append(text)
    return "; ".join(output)
