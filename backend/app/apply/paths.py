from __future__ import annotations

from pathlib import Path

from app.config import BASE_DIR

# BASE_DIR is backend/; data/ lives at the repository root.
REPO_ROOT = BASE_DIR.parent
DATA_DIR = REPO_ROOT / "data"
PROFILE_PATH = DATA_DIR / "applicant.json"
TRACKING_PATH = DATA_DIR / "applications.json"
SCREENSHOT_DIR = DATA_DIR / "applications"
DEFAULT_RESUME = DATA_DIR / "Anurag_Rai_Resume.docx"


def resolve_data_path(value: str | Path) -> Path:
    """Resolve a configured path against the repo root when it is relative."""
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = REPO_ROOT / path
    return path
