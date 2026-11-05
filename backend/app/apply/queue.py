from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from curl_cffi import requests as curl_requests

from app.apply.paths import TRACKING_PATH

SOURCE_COMPANIES = {
    "medallion": "Medallion",
    "hubspot": "HubSpot",
}
TRACKING_DEFAULT = TRACKING_PATH


def load_tracking(path: str | Path | None = None) -> dict[str, Any]:
    tracking_path = Path(path or TRACKING_DEFAULT).expanduser()
    if not tracking_path.exists():
        return {"applications": {}}
    try:
        payload = json.loads(tracking_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"applications": {}}
    if not isinstance(payload, dict):
        return {"applications": {}}
    applications = payload.get("applications")
    if not isinstance(applications, dict):
        payload["applications"] = {}
    return payload


def save_tracking(payload: dict[str, Any], path: str | Path | None = None) -> None:
    tracking_path = Path(path or TRACKING_DEFAULT).expanduser()
    tracking_path.parent.mkdir(parents=True, exist_ok=True)
    tracking_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def submitted_today(tracking: dict[str, Any], today: date | None = None) -> int:
    today = today or date.today()
    count = 0
    for entry in tracking.get("applications", {}).values():
        if not isinstance(entry, dict):
            continue
        if entry.get("status") != "submitted":
            continue
        attempted = str(entry.get("attempted_at") or "")
        if attempted[:10] == today.isoformat():
            count += 1
    return count


def fetch_jobs(
    *,
    api_base: str,
    sources: list[str],
    min_fit: int,
    page_size: int = 100,
) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    base = api_base.rstrip("/")
    for source in sources:
        company = SOURCE_COMPANIES.get(source)
        if not company:
            continue
        page = 1
        while True:
            response = curl_requests.get(
                f"{base}/api/jobs",
                params={
                    "company": company,
                    "min_fit": min_fit,
                    "page": page,
                    "page_size": page_size,
                },
                timeout=30,
            )
            response.raise_for_status()
            payload = response.json()
            items = payload.get("items") or []
            for item in items:
                item["source"] = source
                jobs.append(item)
            if len(items) < page_size:
                break
            page += 1
    return [job for job in jobs if "greenhouse.io" in str(job.get("apply_url") or "")]


def select_queue(
    jobs: list[dict[str, Any]],
    tracking: dict[str, Any],
    *,
    daily_limit: int,
    remaining_today: int | None = None,
    max_attempts: int = 2,
) -> list[dict[str, Any]]:
    applications = tracking.get("applications", {})
    candidates: list[dict[str, Any]] = []
    for job in jobs:
        key = f"{job.get('source')}:{job.get('external_id') or job.get('job_code')}"
        entry = applications.get(key)
        if isinstance(entry, dict):
            status = entry.get("status")
            if status in {"submitted", "submitting", "skipped", "needs_manual"}:
                continue
            if status == "failed" and int(entry.get("attempts") or 0) >= max_attempts:
                continue
            if status == "needs_code" and int(entry.get("attempts") or 0) >= max_attempts:
                continue
        candidates.append(job)

    candidates.sort(key=lambda job: str(job.get("posted_date") or ""), reverse=True)
    candidates.sort(key=lambda job: -(job.get("fit_score") or 0))
    limit = max(0, daily_limit if remaining_today is None else remaining_today)
    return candidates[:limit]


def application_key(job: dict[str, Any]) -> str:
    return f"{job.get('source')}:{job.get('external_id') or job.get('job_code')}"
