from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.apply.paths import PROFILE_PATH, resolve_data_path

PROFILE_DEFAULT = PROFILE_PATH
LINKEDIN_MARKERS = ("linkedin", "linked in")


class ProfileError(ValueError):
    pass


@dataclass(slots=True)
class ApplicantProfile:
    first_name: str
    last_name: str
    email: str
    phone: str
    country: str
    location: str
    linkedin: str
    resume_path: str
    us_work_authorized: bool = False
    python_experience: bool = True
    startup_experience: bool = True
    years_experience: int = 5
    production_ownership: bool = True
    django_experience: bool = False
    tech_stack: str = ""
    why_template: str = ""
    experience_template: str = ""
    preferred_name: str = ""
    github: str = ""
    confirmed: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    def validate(self, *, live: bool = False) -> None:
        missing = [
            name
            for name in ("first_name", "last_name", "email", "phone", "country", "location")
            if not str(getattr(self, name) or "").strip()
        ]
        if missing:
            raise ProfileError("Missing required applicant fields: " + ", ".join(missing))
        if live and not str(self.linkedin or "").strip():
            raise ProfileError(
                "linkedin is required for live submissions; add it to data/applicant.json"
            )
        if live and not self.confirmed:
            raise ProfileError(
                "Set \"confirmed\": true in data/applicant.json after reviewing the profile"
            )
        if live and not resolve_data_path(self.resume_path).exists():
            raise ProfileError(f"Resume not found at {self.resume_path}")

    def answer(self, label: str, *, company: str, title: str) -> str | None:
        """Map a Greenhouse question label to a truthful answer."""
        text = (label or "").strip().lower()
        if not text:
            return None
        if any(marker in text for marker in LINKEDIN_MARKERS):
            return self.linkedin
        if "github" in text:
            return self.github
        if "why" in text and ("work" in text or "join" in text or "company" in text):
            return self.why_answer(company=company, title=title)
        if "tell us about your experience" in text or (
            "experience" in text and "relates" in text
        ):
            return self.experience_answer(company=company, title=title)
        if "tech stack" in text or "technologies" in text or "technology" in text:
            return self.tech_stack
        if "python" in text and ("experience" in text or "using" in text):
            return "Yes" if self.python_experience else "No"
        if "startup" in text:
            return "Yes" if self.startup_experience else "No"
        if "years" in text and "experience" in text:
            match = re.search(r"(\d+)\s*\+?\s*years?", text)
            if match:
                required = int(match.group(1))
                return "Yes" if self.years_experience >= required else "No"
        if "django" in text:
            return "Yes" if self.django_experience else "No"
        if "shipped production code" in text or (
            "owning projects" in text and "production" in text
        ):
            return "Yes" if self.production_ownership else "No"
        if "authorized to work in the united states" in text:
            return "Yes" if self.us_work_authorized else "No"
        if "sponsorship" in text:
            return "No" if self.us_work_authorized else "Yes"
        if "current location" in text or text == "location":
            return self.location
        return None

    def why_answer(self, *, company: str, title: str) -> str:
        if self.why_template:
            try:
                return self.why_template.format(company=company, title=title)
            except (KeyError, IndexError):
                return self.why_template
        return (
            f"My background building data platforms, internal tools, and LLM-driven "
            f"automation maps closely to the {title} role at {company}. I would like "
            f"to bring that experience to a team solving operational problems at scale."
        )

    def experience_answer(self, *, company: str, title: str) -> str:
        if self.experience_template:
            try:
                return self.experience_template.format(company=company, title=title)
            except (KeyError, IndexError):
                return self.experience_template
        return (
            "I have 5 years building data platforms and automation: ETL pipelines "
            "in Python and BigQuery, internal Next.js tools, and LLM-driven triage "
            f"at CertifyOS. That background maps directly to the {title} role at "
            f"{company}."
        )


def load_profile(path: str | Path | None = None) -> ApplicantProfile:
    profile_path = Path(path or PROFILE_DEFAULT).expanduser()
    if not profile_path.exists():
        raise ProfileError(
            f"Applicant profile not found at {profile_path}. "
            "Copy backend/applicant.example.json to data/applicant.json and fill it in."
        )
    try:
        payload = json.loads(profile_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ProfileError(f"{profile_path} is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ProfileError(f"{profile_path} must contain a JSON object")

    known = {
        "first_name",
        "last_name",
        "email",
        "phone",
        "country",
        "location",
        "linkedin",
        "resume_path",
        "us_work_authorized",
        "python_experience",
        "startup_experience",
        "years_experience",
        "production_ownership",
        "django_experience",
        "tech_stack",
        "why_template",
        "experience_template",
        "preferred_name",
        "github",
        "confirmed",
    }
    profile = ApplicantProfile(
        first_name=str(payload.get("first_name") or "").strip(),
        last_name=str(payload.get("last_name") or "").strip(),
        email=str(payload.get("email") or "").strip(),
        phone=str(payload.get("phone") or "").strip(),
        country=str(payload.get("country") or "").strip(),
        location=str(payload.get("location") or "").strip(),
        linkedin=str(payload.get("linkedin") or "").strip(),
        resume_path=str(payload.get("resume_path") or "data/Anurag_Rai_Resume.docx"),
        us_work_authorized=bool(payload.get("us_work_authorized", False)),
        python_experience=bool(payload.get("python_experience", True)),
        startup_experience=bool(payload.get("startup_experience", True)),
        years_experience=int(payload.get("years_experience") or 5),
        production_ownership=bool(payload.get("production_ownership", True)),
        django_experience=bool(payload.get("django_experience", False)),
        tech_stack=str(payload.get("tech_stack") or "").strip(),
        why_template=str(payload.get("why_template") or "").strip(),
        experience_template=str(payload.get("experience_template") or "").strip(),
        preferred_name=str(payload.get("preferred_name") or "").strip(),
        github=str(payload.get("github") or "").strip(),
        confirmed=bool(payload.get("confirmed", False)),
        extra={key: value for key, value in payload.items() if key not in known},
    )
    return profile
