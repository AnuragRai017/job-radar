from __future__ import annotations

import json
from datetime import date

import pytest

from app.apply.autofill import match_field
from app.apply.greenhouse import ApplyError, parse_greenhouse_url
from app.apply.otp import extract_code
from app.apply.profile import ProfileError, load_profile
from app.apply.queue import application_key, select_queue, submitted_today


def _profile_payload(**overrides):
    payload = {
        "first_name": "Anurag",
        "last_name": "Rai",
        "email": "raianu.work@gmail.com",
        "phone": "+919651339863",
        "country": "India",
        "location": "Bengaluru, Karnataka, India",
        "linkedin": "https://www.linkedin.com/in/example",
        "resume_path": "data/Anurag_Rai_Resume.docx",
        "us_work_authorized": False,
        "python_experience": True,
        "tech_stack": "Python, SQL, BigQuery",
        "confirmed": True,
    }
    payload.update(overrides)
    return payload


def test_profile_requires_linkedin_and_confirmation_for_live(tmp_path) -> None:
    path = tmp_path / "applicant.json"
    path.write_text(json.dumps(_profile_payload(linkedin="")), encoding="utf-8")
    profile = load_profile(path)
    profile.validate(live=False)
    with pytest.raises(ProfileError, match="linkedin"):
        profile.validate(live=True)

    path.write_text(json.dumps(_profile_payload(confirmed=False)), encoding="utf-8")
    with pytest.raises(ProfileError, match="confirmed"):
        load_profile(path).validate(live=True)


def test_profile_answers_common_questions(tmp_path) -> None:
    path = tmp_path / "applicant.json"
    path.write_text(json.dumps(_profile_payload()), encoding="utf-8")
    profile = load_profile(path)

    assert profile.answer(
        "LinkedIn Profile", company="Medallion", title="Software Engineer"
    ) == "https://www.linkedin.com/in/example"
    assert profile.answer(
        "Do you have experience with Python?", company="Medallion", title="SWE"
    ) == "Yes"
    assert profile.answer(
        "Do you have over 3 years of experience as a software engineer?",
        company="Medallion",
        title="SWE",
    ) == "Yes"
    assert profile.answer(
        "Do you have over 8 years of experience as a software engineer?",
        company="Medallion",
        title="SWE",
    ) == "No"
    assert profile.answer(
        "Are you legally authorized to work in the United States without employer sponsorship now or in the future?",
        company="Medallion",
        title="SWE",
    ) == "No"
    why = profile.answer(
        "Why do you want to work at Medallion?", company="Medallion", title="SWE"
    )
    assert why and "Medallion" in why
    assert profile.answer("Favorite colour", company="X", title="Y") is None


def test_select_queue_caps_and_skips_finished_applications() -> None:
    jobs = [
        {"source": "medallion", "external_id": "1", "fit_score": 60, "posted_date": "2026-01-01"},
        {"source": "medallion", "external_id": "2", "fit_score": 90, "posted_date": "2026-01-02"},
        {"source": "medallion", "external_id": "3", "fit_score": 70, "posted_date": "2026-01-03"},
    ]
    tracking = {
        "applications": {
            "medallion:2": {"status": "submitted"},
            "medallion:3": {"status": "failed", "attempts": 2},
        }
    }
    queue = select_queue(jobs, tracking, daily_limit=10)
    assert [job["external_id"] for job in queue] == ["1"]
    assert application_key(jobs[0]) == "medallion:1"

    limited = select_queue(jobs, {"applications": {}}, daily_limit=10, remaining_today=1)
    assert [job["external_id"] for job in limited] == ["2"]


def test_submitted_today_counts_only_submissions() -> None:
    today = date(2026, 11, 5)
    tracking = {
        "applications": {
            "a": {"status": "submitted", "attempted_at": "2026-11-05T06:00:00+00:00"},
            "b": {"status": "submitted", "attempted_at": "2026-11-04T06:00:00+00:00"},
            "c": {"status": "failed", "attempted_at": "2026-11-05T07:00:00+00:00"},
        }
    }
    assert submitted_today(tracking, today=today) == 1


def test_extract_code_from_verification_text() -> None:
    body = (
        "Copy and paste this code into the security code field on your "
        "application: 50833972 After you enter the code, resubmit your application."
    )
    assert extract_code(body) == "50833972"
    assert extract_code("Your verification code is 12345678. It expires soon.") == "12345678"
    assert extract_code("No code here") is None


def test_parse_greenhouse_url() -> None:
    assert parse_greenhouse_url(
        "https://job-boards.greenhouse.io/medallionakafirstlayerai/jobs/4063902009"
    ) == ("medallionakafirstlayerai", "4063902009")
    with pytest.raises(ApplyError):
        parse_greenhouse_url("https://example.com/jobs/1")


def test_match_field_maps_common_ats_labels() -> None:
    assert match_field("First Name") == "first_name"
    assert match_field("Last Name") == "last_name"
    assert match_field("Email Address") == "email"
    assert match_field("Mobile Phone") == "phone"
    assert match_field("LinkedIn Profile") == "linkedin"
    assert match_field("Resume/CV") == "resume"
    assert match_field("Country") == "country"
    assert match_field("City") == "location"
    assert match_field("Years of experience") == "years_experience"
    assert (
        match_field(
            "Are you legally authorized to work in the United States without employer sponsorship?"
        )
        == "us_work_authorized"
    )
    assert (
        match_field("Will you now or in the future require sponsorship?") is None
    )
    assert (
        match_field("Will you require sponsorship to work in the United States?")
        == "sponsorship"
    )
    assert match_field("Favorite colour") is None
