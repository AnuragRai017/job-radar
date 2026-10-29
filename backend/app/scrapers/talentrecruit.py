from __future__ import annotations

import base64
import json
from typing import Any
from urllib.parse import quote

from app.scrapers.base import FetchError, HttpClient
from app.scrapers.models import BaseScraper, JobRecord, ScrapeContext, ScrapeResult
from app.utils import clean_text, parse_date, safe_http_url

API_ROOT = "https://app.api.talentrecruit.com/api/v1"
CAREER_ROOT = "https://appcareer.talentrecruit.com"
# Embedded in the public career-page bundle. It decrypts the API's NaCl box.
SECRET_KEY = bytes(
    [
        98,
        89,
        42,
        106,
        113,
        112,
        94,
        50,
        73,
        100,
        114,
        53,
        108,
        83,
        52,
        52,
        87,
        73,
        89,
        98,
        75,
        56,
        121,
        90,
        57,
        86,
        68,
        86,
        94,
        68,
        85,
        113,
    ]
)


class TalentRecruitScraper(BaseScraper):
    """TalentRecruit public career-page API (NaCl-encrypted JSON).

    KPMG India's board is a TalentRecruit career page. The JSON response is
    encrypted with a NaCl box whose public client key is embedded in the page's
    JavaScript; decrypting it is the same operation the browser performs.
    """

    shortname = ""
    page_size = 200
    default_max_pages = 5

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
                    payload = await self._fetch_page(http, offset)
                except (FetchError, ValueError, KeyError) as exc:
                    if page_number == 1:
                        raise
                    result.complete = False
                    result.note(f"List page {page_number} failed: {exc}")
                    break

                inner = self._inner_payload(payload)
                jobs = inner.get("data") or []
                if total is None:
                    count = inner.get("noOfTotalRecords")
                    try:
                        total = int(count.get("count") or 0) if isinstance(count, dict) else 0
                    except (TypeError, ValueError):
                        total = 0
                    self.log.info("%s: %d roles reported", self.company, total)
                if not jobs:
                    break

                for item in jobs:
                    if self.limit_reached(ctx, result.seen_ids):
                        result.complete = False
                        result.note("Max jobs per source reached")
                        break
                    job = self._parse_job(item)
                    if job is None or job.external_id in result.seen_ids:
                        continue
                    result.seen_ids.add(job.external_id)
                    result.jobs.append(job)

                offset += len(jobs)
                if total and offset >= total:
                    break
                if len(jobs) < self.page_size:
                    break
                page_number += 1
            else:
                result.complete = False
                result.note("Page limit reached")

        self.log.info(
            "%s: found=%d, errors=%d, complete=%s",
            self.company,
            len(result.seen_ids),
            result.errors,
            result.complete,
        )
        return result

    async def _fetch_page(self, http: HttpClient, offset: int) -> dict[str, Any]:
        url = (
            f"{API_ROOT}/career/template/job/list"
            f"?limit={self.page_size}&offset={offset}"
        )
        headers = {
            "Accept": "application/json",
            "shortname": f"https://{self.shortname}.talentrecruit.com",
            "Origin": CAREER_ROOT,
            "Referer": f"{CAREER_ROOT}/",
        }
        response = await http.get(url, expect_json=True, headers=headers)
        encrypted = response.json()
        if not isinstance(encrypted, dict):
            raise FetchError(url, "unexpected TalentRecruit payload")
        return self._decrypt(encrypted)

    @staticmethod
    def _decrypt(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            from nacl.public import Box, PrivateKey, PublicKey
        except ImportError as exc:  # pragma: no cover - runtime dependency
            raise FetchError(
                API_ROOT, "PyNaCl is required to decrypt TalentRecruit responses"
            ) from exc

        try:
            nonce = base64.b64decode(payload["key"])
            ciphertext = base64.b64decode(payload["text"])
            peer = PublicKey(base64.b64decode(payload["iv"]))
            box = Box(PrivateKey(SECRET_KEY), peer)
            plain = box.decrypt(ciphertext, nonce)
            data = json.loads(plain.decode("utf-8"))
        except Exception as exc:  # noqa: BLE001 - any decrypt failure is a fetch error
            raise FetchError(API_ROOT, f"could not decrypt TalentRecruit payload: {exc}") from exc
        if not isinstance(data, dict):
            raise FetchError(API_ROOT, "decrypted TalentRecruit payload was not an object")
        return data

    @staticmethod
    def _inner_payload(payload: dict[str, Any]) -> dict[str, Any]:
        data = payload.get("data")
        if not isinstance(data, dict):
            return {}
        inner = data.get("data")
        return inner if isinstance(inner, dict) else {}

    def _parse_job(self, item: dict[str, Any]) -> JobRecord | None:
        external_id = clean_text(item.get("jobid"), max_length=160) or clean_text(
            item.get("code"), max_length=80
        )
        title = clean_text(item.get("title"), max_length=500)
        if not external_id or not title:
            return None

        description_parts: list[str] = []
        seen: set[str] = set()
        for heading, key in (
            ("Role description", "description"),
            ("Qualifications", "qualificationcriteria"),
            ("Additional information", "additionalinformation"),
        ):
            value = str(item.get(key) or "").strip()
            if not value or value in seen:
                continue
            seen.add(value)
            description_parts.append(f"<h3>{heading}</h3>\n{value}")
        if not description_parts:
            return None

        location = clean_text(item.get("joblocation"), max_length=1000) or clean_text(
            item.get("city"), max_length=1000
        )
        country = clean_text(item.get("country"), max_length=120)
        if country and country.lower() not in location.lower():
            location = f"{location}, {country}" if location else country

        apply_url = safe_http_url(
            f"{CAREER_ROOT}/career-page/apply/{quote(external_id, safe='')}",
            f"https://{self.shortname}.talentrecruit.com/career-page",
        )
        posted = parse_date(item.get("publishedtime")) or parse_date(
            item.get("createdtime")
        )

        return JobRecord(
            source=self.source,
            company=self.company,
            external_id=external_id,
            title=title,
            location=location,
            description_html="\n".join(description_parts),
            apply_url=apply_url,
            posted_date=posted,
        )


class KPMGIndiaScraper(TalentRecruitScraper):
    source = "kpmg_india"
    company = "KPMG India"
    shortname = "kpmgindia"
    default_max_pages = 2
