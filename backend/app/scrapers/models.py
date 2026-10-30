from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date

from app.scrapers.base import HttpClient


@dataclass(slots=True)
class KnownJob:
    title: str
    location: str
    posted_date: date | None
    has_description: bool
    job_code: str = ""


@dataclass(slots=True)
class JobRecord:
    source: str
    company: str
    external_id: str
    title: str
    location: str
    description_html: str
    apply_url: str
    posted_date: date | None = None
    job_code: str = ""


@dataclass
class ScrapeContext:
    known: dict[str, KnownJob] = field(default_factory=dict)
    max_jobs: int = 0
    max_pages: int = 0


@dataclass
class ScrapeResult:
    jobs: list[JobRecord] = field(default_factory=list)
    seen_ids: set[str] = field(default_factory=set)
    complete: bool = True
    notes: list[str] = field(default_factory=list)
    errors: int = 0
    details_fetched: int = 0

    def note(self, message: str) -> None:
        if message not in self.notes:
            self.notes.append(message)


class BaseScraper(ABC):
    source: str = ""
    company: str = ""
    profile: str = "desktop"
    default_max_pages: int = 500

    def __init__(self) -> None:
        import logging

        self.log = logging.getLogger(f"scraper.{self.source}")

    @abstractmethod
    async def scrape(self, ctx: ScrapeContext) -> ScrapeResult:
        raise NotImplementedError

    def max_pages(self, ctx: ScrapeContext) -> int:
        return ctx.max_pages or self.default_max_pages

    def limit_reached(self, ctx: ScrapeContext, seen: set[str]) -> bool:
        return bool(ctx.max_jobs and len(seen) >= ctx.max_jobs)

    def needs_detail(
        self,
        ctx: ScrapeContext,
        external_id: str,
        *,
        title: str,
        location: str = "",
        posted_date: date | None = None,
        compare_location: bool = False,
    ) -> bool:
        known = ctx.known.get(external_id)
        if known is None or not known.has_description:
            return True
        # Refresh once to populate the native referral ID for older rows.
        if not known.job_code:
            return True
        if known.title.strip() != title.strip():
            return True
        if (
            compare_location
            and location
            and location.strip().lower()
            not in {"multiple locations", "various locations", "various"}
            and known.location.strip() != location.strip()
        ):
            return True
        # A known job with a description is unchanged enough to skip the detail request.
        return posted_date is not None and known.posted_date != posted_date

    def new_client(self) -> HttpClient:
        return HttpClient(profile=self.profile)
