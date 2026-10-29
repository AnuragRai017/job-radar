from __future__ import annotations

import hashlib
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree

from app.config import settings

# Canonical skill names and the phrases that indicate them in a resume.
SKILL_ALIASES: dict[str, tuple[str, ...]] = {
    "Python": ("python",),
    "SQL": (" sql", "sql,"),
    "BigQuery": ("bigquery", "big query"),
    "ETL": ("etl", "elt"),
    "Data pipelines": ("data pipeline", "data pipelines", "ingestion"),
    "Data modeling": ("data model", "data modeling", "dimensional model"),
    "Dashboards": ("dashboard", "power bi", "looker", "tableau"),
    "TypeScript": ("typescript",),
    "JavaScript": ("javascript",),
    "NLP": ("nlp", "natural language"),
    "LLM": ("llm", "large language model", "generative ai", "genai"),
    "Prompt engineering": ("prompt engineering", "prompt design"),
    "AI agents": ("ai agent", "ai agents", "agentic", "mcp", "model context protocol"),
    "REST APIs": ("rest api", "rest apis", "api integration", "apis"),
    "GCP": ("gcp", "google cloud"),
    "Cloud Run": ("cloud run",),
    "Cloud Functions": ("cloud function", "cloud functions"),
    "GitHub Actions": ("github actions", "github workflow", "ci/cd"),
    "Jira": ("jira",),
    "ServiceNow": ("servicenow", "service now"),
    "Next.js": ("next.js", "nextjs"),
    "React": ("react",),
    "Node.js": ("node.js", "nodejs"),
    "Machine learning": ("machine learning", "ml model", "ml-based", "ml based"),
    "Sentiment analysis": ("sentiment analysis",),
    "Automation": ("automation", "automate", "automated"),
    "DevOps": ("devops", "dev ops", "continuous integration", "continuous deployment"),
    "AI Tools": ("Claude Code", "Codex", "GitHub Copilot", "Cursor ai", "OpenAI"),
}

TARGET_TITLES = (
    "data engineer",
    "platform engineer",
    "analytics engineer",
    "data platform",
    "data analyst",
    "business intelligence",
    "bi developer",
    "ai engineer",
    "machine learning engineer",
    "ml engineer",
    "software engineer",
    "backend engineer",
    "automation engineer",
    "technical support engineer",
    "support engineer",
    "cloud engineer",
    "data scientist",
    "integration engineer",
    "solutions engineer",
    "application engineer",
    "python developer",
)

PREFERRED_LOCATIONS = (
    "india",
    "bengaluru",
    "bangalore",
    "mumbai",
    "pune",
    "hyderabad",
    "chennai",
    "gurgaon",
    "gurugram",
    "noida",
    "delhi",
    "kolkata",
    "ahmedabad",
    "jaipur",
    "kochi",
    "coimbatore",
    "indore",
    "chandigarh",
    "remote",
    "anywhere",
)


@dataclass(frozen=True, slots=True)
class ResumeProfile:
    raw_text: str
    skills: tuple[str, ...]
    target_titles: tuple[str, ...]
    preferred_locations: tuple[str, ...]
    years_experience: float
    summary: str
    hash: str

    def prompt_text(self, max_chars: int = 3_500) -> str:
        """Compact resume text without contact details."""
        lines = []
        for line in self.raw_text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if "@" in stripped or re.search(r"\+?\d[\d\s-]{8,}\d", stripped):
                continue
            lines.append(stripped)
        text = "\n".join(lines)
        return text[:max_chars]


def _read_docx(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        try:
            document = archive.read("word/document.xml")
        except KeyError as exc:
            raise ValueError(f"{path} is not a valid .docx file") from exc

    namespace = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    root = ElementTree.fromstring(document)
    paragraphs: list[str] = []
    for paragraph in root.iter(f"{{{namespace}}}p"):
        text = "".join(
            node.text or "" for node in paragraph.iter(f"{{{namespace}}}t")
        ).strip()
        if text:
            paragraphs.append(text)
    return "\n".join(paragraphs)


def read_resume_text(path: str | Path) -> str:
    resume_path = Path(path).expanduser()
    if not resume_path.exists():
        raise FileNotFoundError(
            f"Resume not found at {resume_path}. Set RESUME_PATH or pass --resume."
        )
    suffix = resume_path.suffix.lower()
    if suffix == ".docx":
        text = _read_docx(resume_path)
    elif suffix in {".txt", ".md"}:
        text = resume_path.read_text(encoding="utf-8", errors="replace")
    else:
        raise ValueError("Resume must be a .docx, .txt, or .md file")
    if not text.strip():
        raise ValueError(f"Resume at {resume_path} is empty")
    return text


def _summary(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for index, line in enumerate(lines):
        if line.upper() == "PROFESSIONAL SUMMARY" and index + 1 < len(lines):
            return lines[index + 1][:1_200]
    return " ".join(lines[:3])[:1_200]


def _years_experience(text: str) -> float:
    patterns = (
        r"(\d+(?:\.\d+)?)\s*\+\s*years?",
        r"(\d+(?:\.\d+)?)\s+years?",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            try:
                return float(match.group(1))
            except ValueError:
                continue
    return 0.0


def build_profile(text: str) -> ResumeProfile:
    lowered = text.lower()
    skills = tuple(
        skill
        for skill, aliases in SKILL_ALIASES.items()
        if any(alias in lowered for alias in aliases)
    )
    return ResumeProfile(
        raw_text=text,
        skills=skills,
        target_titles=TARGET_TITLES,
        preferred_locations=PREFERRED_LOCATIONS,
        years_experience=_years_experience(text),
        summary=_summary(text),
        hash=hashlib.sha256(text.encode("utf-8")).hexdigest()[:32],
    )


def load_resume(path: str | Path | None = None) -> ResumeProfile:
    return build_profile(read_resume_text(path or settings.resume_path))
