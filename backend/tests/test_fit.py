from __future__ import annotations

from app.fit.resume import build_profile, read_resume_text
from app.fit.scoring import FitScorer, heuristic_fit, run_fit_scoring
from app.models import Job

RESUME_TEXT = """\
ANURAG RAI
PROFESSIONAL SUMMARY
Data & Platform Engineer with 4+ years of experience building ETL pipelines,
analytics platforms, and LLM automation.
CORE SKILLS
Languages: Python, SQL, TypeScript, JavaScript
Data: ETL, BigQuery, Data Pipelines, Data Modeling, Dashboards
AI: NLP, LLM, Prompt Engineering, AI Agents, MCP
Cloud: GCP, Cloud Run, Cloud Functions, GitHub Actions
Backend: REST APIs, Python Automation
Frontend: Next.js, React
"""


def test_profile_extracts_skills_and_experience() -> None:
    profile = build_profile(RESUME_TEXT)
    assert "Python" in profile.skills
    assert "SQL" in profile.skills
    assert "BigQuery" in profile.skills
    assert "LLM" in profile.skills
    assert "GCP" in profile.skills
    assert profile.years_experience == 4
    assert len(profile.hash) == 32
    assert "Data & Platform Engineer" in profile.summary


def test_heuristic_fit_prefers_data_roles_in_india() -> None:
    profile = build_profile(RESUME_TEXT)
    good = heuristic_fit(
        job_id=1,
        company="Acme",
        title="Data Platform Engineer",
        location="Bengaluru, India",
        description="Build Python and SQL ETL pipelines in BigQuery on GCP.",
        profile=profile,
    )
    weak = heuristic_fit(
        job_id=2,
        company="Acme",
        title="Sales Manager",
        location="New York, United States",
        description="Own quota and enterprise sales relationships.",
        profile=profile,
    )
    assert good.score >= 70
    assert weak.score < 50
    assert good.score > weak.score
    assert any("Python" in reason for reason in good.reasons)


def test_fit_scoring_persists_heuristic_scores(db, tmp_path) -> None:
    resume = tmp_path / "resume.txt"
    resume.write_text(RESUME_TEXT, encoding="utf-8")
    assert read_resume_text(resume).startswith("ANURAG RAI")

    db.add_all(
        [
            Job(
                source="test",
                company="Acme",
                external_id="data-1",
                title="Data Engineer",
                location="Bengaluru, India",
                description_html="<p>Python SQL BigQuery ETL</p>",
                description_text="Python SQL BigQuery ETL data pipelines",
                apply_url="https://example.com/data-1",
            ),
            Job(
                source="test",
                company="Acme",
                external_id="sales-1",
                title="Sales Manager",
                location="New York, United States",
                description_html="<p>Enterprise sales quota.</p>",
                description_text="Enterprise sales quota and account management",
                apply_url="https://example.com/sales-1",
            ),
        ]
    )
    db.commit()

    summary = run_fit_scoring(use_llm=False, resume_path=str(resume))
    assert summary.scanned == 2
    assert summary.heuristic_saved == 2
    assert summary.llm_scored == 0

    db.expire_all()
    jobs = {job.external_id: job for job in db.query(Job).all()}
    assert jobs["data-1"].fit_score is not None
    assert jobs["data-1"].fit_method == "heuristic"
    assert jobs["data-1"].fit_score > jobs["sales-1"].fit_score
    assert jobs["data-1"].fit_reasons

    second = run_fit_scoring(use_llm=False, resume_path=str(resume))
    assert second.heuristic_saved == 0
    assert second.llm_scored == 0


def test_fit_scorer_reports_circuit_state() -> None:
    scorer = FitScorer(max_jobs=1)
    assert not scorer.circuit_open
    scorer._fail("test failure")
    assert not scorer.circuit_open
    scorer._fail("second failure")
    scorer._fail("third failure")
    assert scorer.circuit_open
