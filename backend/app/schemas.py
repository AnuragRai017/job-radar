from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from app.utils import iso_utc


class ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class JobSummary(ApiModel):
    id: int
    source: str
    company: str
    external_id: str
    title: str
    location: str
    apply_url: str
    posted_date: date | None = None
    excerpt: str = ""
    is_active: bool = True
    fit_score: int | None = None
    fit_verdict: str | None = None
    scraped_at: datetime

    @field_serializer("scraped_at")
    def _serialize_scraped_at(self, value: datetime) -> str | None:
        return iso_utc(value)


class JobDetail(JobSummary):
    description_html: str
    description_text: str
    first_seen_at: datetime
    last_seen_at: datetime
    fit_reasons: list[str] = Field(default_factory=list)
    fit_gaps: list[str] = Field(default_factory=list)
    fit_method: str | None = None
    fit_model: str | None = None
    fit_scored_at: datetime | None = None

    @field_serializer("first_seen_at", "last_seen_at", "fit_scored_at")
    def _serialize_dates(self, value: datetime | None) -> str | None:
        return iso_utc(value) if value is not None else None


class JobListResponse(ApiModel):
    items: list[JobSummary]
    total: int
    page: int
    page_size: int
    pages: int


class CompanyStat(ApiModel):
    company: str
    source: str
    count: int
    active_count: int


class RunSummary(ApiModel):
    id: int
    trigger: str
    status: str
    started_at: datetime
    finished_at: datetime | None = None
    total_found: int = 0
    total_upserted: int = 0
    total_errors: int = 0
    sources: dict[str, Any] = Field(default_factory=dict)

    @field_serializer("started_at", "finished_at")
    def _serialize_run_dates(self, value: datetime | None) -> str | None:
        return iso_utc(value)


class StatsResponse(ApiModel):
    total_jobs: int
    active_jobs: int
    fit_scored_jobs: int = 0
    fit_best_score: int | None = None
    companies: list[CompanyStat]
    last_scrape_at: datetime | None = None
    last_successful_scrape_at: datetime | None = None
    last_run: RunSummary | None = None

    @field_serializer("last_scrape_at", "last_successful_scrape_at")
    def _serialize_stats_dates(self, value: datetime | None) -> str | None:
        return iso_utc(value)


class HealthResponse(ApiModel):
    status: str
    database: str
    version: str
    last_scrape_at: datetime | None = None

    @field_serializer("last_scrape_at")
    def _serialize_health_date(self, value: datetime | None) -> str | None:
        return iso_utc(value)
