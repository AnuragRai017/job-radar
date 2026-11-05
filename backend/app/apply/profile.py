from __future__ import annotations

import json
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
    tech_stack: str = ""
    why_template: str = ""
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
        if "tech stack" in text or "technologies" in text or "technology" in text:
            return self.tech_stack
        if "python" in text and ("experience" in text or "using" in text):
            return "Yes" if self.python_experience else "No"
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
        "tech_stack",
        "why_template",
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
        tech_stack=str(payload.get("tech_stack") or "").strip(),
        why_template=str(payload.get("why_template") or "").strip(),
        preferred_name=str(payload.get("preferred_name") or "").strip(),
        github=str(payload.get("github") or "").strip(),
        confirmed=bool(payload.get("confirmed", False)),
        extra={key: value for key, value in payload.items() if key not in known},
    )
    return profile
