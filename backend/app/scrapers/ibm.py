from __future__ import annotations

import re
from typing import Any

from app.scrapers.base import FetchError, HttpClient
from app.scrapers.models import BaseScraper, JobRecord, ScrapeContext, ScrapeResult
from app.utils import clean_text, parse_date, safe_http_url

IBM_SEARCH_URL = "https://www-api.ibm.com/search/api/v2"
IBM_JOB_URL = "https://careers.ibm.com/careers/JobDetail?jobId={job_id}"
IBM_JOB_ID_RE = re.compile(r"[?&]jobId=(\d+)", re.IGNORECASE)

# The search API is capped at 100 hits per request.
IBM_PAGE_SIZE = 100

# IBM's index stores a subset of descriptions as UTF-8 bytes that were decoded
# as cp1252 (e.g. "Youâ€™ll"). Decode the common sequences back to text.
_MOJIBAKE_CHARACTERS = "’‘“”–—…•éèáàóúñçüöäß€™"
_MOJIBAKE_MAP: dict[str, str] = {}
for _character in _MOJIBAKE_CHARACTERS:
    try:
        _MOJIBAKE_MAP[_character.encode("utf-8").decode("cp1252")] = _character
    except UnicodeDecodeError:  # pragma: no cover - defensive
        continue


class IBMScraper(BaseScraper):
    """IBM careers search API (www-api.ibm.com).

    The public careers page posts an Elasticsearch-style query to
    /search/api/v2. Each hit already carries the full HTML description
    (raw_body), location, and the numeric jobId used in the public URL, so no
    per-job detail request is needed. The detail page itself is bot-challenged.
    """

    source = "ibm"
    company = "IBM"
    page_size = IBM_PAGE_SIZE
    default_max_pages = 20

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
                    payload = await self._fetch_page(http, offset, page_number)
                except (FetchError, ValueError, KeyError) as exc:
                    if page_number == 1:
                        raise
                    result.complete = False
                    result.note(f"List page {page_number} failed: {exc}")
                    self.log.warning(
                        "%s list page %d failed: %s", self.company, page_number, exc
                    )
                    break

                hits, page_total = self._parse_page(payload)
                if total is None:
                    total = page_total
                    self.log.info("%s: %d India roles reported", self.company, total)
                if not hits:
                    break

                for hit in hits:
                    if self.limit_reached(ctx, result.seen_ids):
                        result.complete = False
                        result.note("Max jobs per source reached")
                        break

                    external_id = self._external_id(hit)
                    if not external_id or external_id in result.seen_ids:
                        continue

                    title = self._title(hit)
                    location = self._location(hit)
                    posted = parse_date(hit.get("dcdate"))
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
                        job = self._parse_job(hit, external_id)
                    except ValueError as exc:
                        result.errors += 1
                        self.log.warning(
                            "%s skipped %s: %s", self.company, external_id, exc
                        )
                        continue

                    result.details_fetched += 1
                    result.jobs.append(job)
                    if result.details_fetched % 100 == 0:
                        self.log.info(
                            "%s: %d jobs collected", self.company, result.details_fetched
                        )

                offset += len(hits)
                if total and offset >= total:
                    break
                if len(hits) < self.page_size:
                    break
                page_number += 1
            else:
                result.complete = False
                result.note("Page limit reached")

        self.log.info(
            "%s: found=%d, collected=%d, errors=%d, complete=%s",
            self.company,
            len(result.seen_ids),
            result.details_fetched,
            result.errors,
            result.complete,
        )
        return result

    async def _fetch_page(
        self, http: HttpClient, offset: int, page_number: int
    ) -> dict[str, Any]:
        response = await http.post(
            IBM_SEARCH_URL,
            json=self._query(offset, page_number),
            expect_json=True,
            headers={
                "Accept": "application/json, text/plain, */*",
                "Referer": "https://www.ibm.com/",
                "Origin": "https://www.ibm.com",
            },
        )
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("unexpected IBM search payload")
        if "hits" not in payload:
            raise ValueError("IBM search payload has no hits")
        return payload

    def _query(self, offset: int, page_number: int) -> dict[str, Any]:
        return {
            "appId": "careers",
            "scopes": ["careers2"],
            "query": {"bool": {"must": [{"match": {"country": "in"}}]}},
            "size": self.page_size,
            "from": offset,
            # A unique sort keeps from/size pagination deterministic. The site's
            # default [_score, pageviews] sort ties on every doc and yields
            # duplicate pages (and skipped jobs) as the index refreshes.
            "sort": [{"_id": "asc"}],
            "lang": "zz",
            "localeSelector": {},
            "p": page_number,
            "sm": {"query": "", "lang": "zz"},
            "_source": [
                "title",
                "url",
                "raw_body",
                "description",
                "dcdate",
                "field_keyword_08",
                "field_keyword_17",
                "field_keyword_18",
                "field_keyword_19",
            ],
        }

    @staticmethod
    def _parse_page(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
        hits = payload.get("hits")
        if not isinstance(hits, dict):
            return [], 0
        records: list[dict[str, Any]] = []
        for hit in hits.get("hits") or []:
            source = hit.get("_source") if isinstance(hit, dict) else None
            if isinstance(source, dict):
                records.append(source)
        total = hits.get("total")
        if isinstance(total, dict):
            total = total.get("value")
        try:
            total_count = int(total or 0)
        except (TypeError, ValueError):
            total_count = 0
        return records, total_count

    @staticmethod
    def _external_id(record: dict[str, Any]) -> str:
        match = IBM_JOB_ID_RE.search(str(record.get("url") or ""))
        return match.group(1) if match else ""

    @staticmethod
    def _title(record: dict[str, Any]) -> str:
        title = clean_text(record.get("title"), max_length=500) or "Untitled role"
        # A few listings prefix the country, e.g. "India - Quality Leader".
        return re.sub(r"^India\s*[–-]\s*", "", title)

    @staticmethod
    def _location(record: dict[str, Any]) -> str:
        return clean_text(record.get("field_keyword_19"), max_length=1000) or "India"

    @staticmethod
    def _repair_text(text: str) -> str:
        if not text or not any(bad in text for bad in _MOJIBAKE_MAP):
            return text
        for bad, good in _MOJIBAKE_MAP.items():
            if bad in text:
                text = text.replace(bad, good)
        return text

    def _parse_job(self, record: dict[str, Any], external_id: str) -> JobRecord:
        description = str(record.get("raw_body") or record.get("description") or "").strip()
        if not description:
            raise ValueError("search hit has no description")
        title = self._title(record)
        apply_url = safe_http_url(record.get("url"), IBM_JOB_URL.format(job_id=external_id))
        return JobRecord(
            source=self.source,
            company=self.company,
            external_id=external_id,
            job_code=external_id,
            title=title,
            location=self._location(record),
            description_html=self._repair_text(description),
            apply_url=apply_url,
            posted_date=parse_date(record.get("dcdate")),
        )
