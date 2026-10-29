from __future__ import annotations

from datetime import UTC, date, datetime

from app.models import Job, ScrapeRun


def _seed(db) -> None:
    db.add_all(
        [
            Job(
                source="google",
                company="Google",
                external_id="g1",
                title="Software Engineer",
                location="London, UK",
                description_html="<p>Build <strong>search</strong> systems.</p>",
                description_text="Build search systems.",
                apply_url="https://example.com/g1",
                posted_date=date(2025, 3, 1),
            ),
            Job(
                source="google",
                company="Google",
                external_id="g2",
                title="Data Scientist",
                location="New York, NY",
                description_html="<p>Machine learning models.</p>",
                description_text="Machine learning models.",
                apply_url="https://example.com/g2",
                posted_date=date(2025, 2, 15),
            ),
            Job(
                source="deloitte",
                company="Deloitte",
                external_id="d1",
                title="Cyber Consultant",
                location="London, UK",
                description_html="<p>Security engineer role.</p>",
                description_text="Security engineer role.",
                apply_url="https://example.com/d1",
                posted_date=date(2025, 1, 10),
            ),
            Job(
                source="capgemini",
                company="Capgemini",
                external_id="c1",
                title="Cloud Architect",
                location="Paris, France",
                description_html="<p>Retired role.</p>",
                description_text="Retired role.",
                apply_url="https://example.com/c1",
                posted_date=date(2024, 12, 1),
                is_active=False,
            ),
        ]
    )
    db.add(
        ScrapeRun(
            trigger="scheduler",
            status="success",
            started_at=datetime(2025, 3, 2, 10, 0, tzinfo=UTC),
            finished_at=datetime(2025, 3, 2, 10, 5, tzinfo=UTC),
            total_found=4,
            total_upserted=4,
            total_errors=0,
            sources={"google": {"status": "success"}},
        )
    )
    db.commit()


def test_health(client) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert body["version"]


def test_list_jobs_defaults_to_active_and_newest_first(client, db) -> None:
    _seed(db)
    response = client.get("/api/jobs")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    assert body["page"] == 1
    assert body["pages"] == 1
    assert [item["external_id"] for item in body["items"]] == ["g1", "g2", "d1"]
    assert body["items"][0]["excerpt"] == "Build search systems."
    assert body["items"][0]["posted_date"] == "2025-03-01"


def test_list_jobs_filters(client, db) -> None:
    _seed(db)

    company = client.get("/api/jobs", params={"company": "google"}).json()
    assert company["total"] == 2
    assert {item["company"] for item in company["items"]} == {"Google"}

    location = client.get("/api/jobs", params={"location": "london"}).json()
    assert location["total"] == 2
    assert {item["external_id"] for item in location["items"]} == {"g1", "d1"}

    keyword = client.get("/api/jobs", params={"keyword": "engineer"}).json()
    assert keyword["total"] == 2

    none = client.get("/api/jobs", params={"keyword": "no-such-role"}).json()
    assert none["total"] == 0
    assert none["pages"] == 0
    assert none["items"] == []

    explicit_empty = client.get("/api/jobs", params={"company": ""}).json()
    assert explicit_empty["total"] == 3


def test_list_jobs_include_inactive(client, db) -> None:
    _seed(db)
    body = client.get("/api/jobs", params={"include_inactive": "true"}).json()
    assert body["total"] == 4
    inactive = [item for item in body["items"] if not item["is_active"]]
    assert len(inactive) == 1
    assert inactive[0]["external_id"] == "c1"


def test_list_jobs_pagination(client, db) -> None:
    _seed(db)
    first = client.get("/api/jobs", params={"page_size": 2}).json()
    assert first["total"] == 3
    assert first["pages"] == 2
    assert len(first["items"]) == 2

    second = client.get("/api/jobs", params={"page_size": 2, "page": 2}).json()
    assert second["page"] == 2
    assert [item["external_id"] for item in second["items"]] == ["d1"]

    invalid = client.get("/api/jobs", params={"page": 0})
    assert invalid.status_code == 422
    too_large = client.get("/api/jobs", params={"page_size": 101})
    assert too_large.status_code == 422


def test_job_detail_and_404(client, db) -> None:
    _seed(db)
    listing = client.get("/api/jobs").json()["items"][0]
    response = client.get(f"/api/jobs/{listing['id']}")
    assert response.status_code == 200
    body = response.json()
    assert body["title"] == "Software Engineer"
    assert body["description_html"] == "<p>Build <strong>search</strong> systems.</p>"
    assert body["description_text"] == "Build search systems."
    assert body["first_seen_at"].endswith("Z")
    assert body["scraped_at"].endswith("Z")

    missing = client.get("/api/jobs/999999")
    assert missing.status_code == 404
    assert missing.json()["detail"] == "Job not found"


def test_stats_and_health_use_latest_run(client, db) -> None:
    _seed(db)
    response = client.get("/api/stats")
    assert response.status_code == 200
    body = response.json()
    assert body["total_jobs"] == 4
    assert body["active_jobs"] == 3
    assert body["last_scrape_at"] == "2025-03-02T10:05:00Z"
    assert body["last_successful_scrape_at"] == "2025-03-02T10:05:00Z"
    assert body["last_run"]["status"] == "success"

    companies = {item["company"]: item for item in body["companies"]}
    assert companies["Google"]["active_count"] == 2
    assert companies["Capgemini"]["active_count"] == 0

    health = client.get("/health").json()
    assert health["last_scrape_at"] == "2025-03-02T10:05:00Z"


def test_cors_allows_configured_origin(client) -> None:
    response = client.get("/api/jobs", headers={"Origin": "http://127.0.0.1:4321"})
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == "http://127.0.0.1:4321"


def test_fit_score_filter_sort_and_detail(client, db) -> None:
    _seed(db)
    job = db.query(Job).filter(Job.external_id == "g1").one()
    job.fit_score = 82
    job.fit_verdict = "strong"
    job.fit_method = "llm"
    job.fit_model = "mistral:7b-instruct"
    job.fit_reasons = ["Matches Python and BigQuery.", "India location."]
    job.fit_gaps = ["No production LLM experience listed."]
    db.commit()

    filtered = client.get("/api/jobs", params={"min_fit": 70, "sort": "fit"}).json()
    assert filtered["total"] == 1
    assert filtered["items"][0]["external_id"] == "g1"
    assert filtered["items"][0]["fit_score"] == 82
    assert filtered["items"][0]["fit_verdict"] == "strong"

    detail = client.get(f"/api/jobs/{job.id}").json()
    assert detail["fit_method"] == "llm"
    assert detail["fit_model"] == "mistral:7b-instruct"
    assert detail["fit_reasons"] == [
        "Matches Python and BigQuery.",
        "India location.",
    ]

    stats = client.get("/api/stats").json()
    assert stats["fit_scored_jobs"] == 1
    assert stats["fit_best_score"] == 82
