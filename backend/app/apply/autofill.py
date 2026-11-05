from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.apply.paths import resolve_data_path
from app.apply.profile import ApplicantProfile

RULES: tuple[tuple[str, str], ...] = (
    (r"\bfirst\s*name\b|\bgiven\s*name\b", "first_name"),
    (r"\blast\s*name\b|\bfamily\s*name\b|\bsurname\b", "last_name"),
    (r"\bpreferred\s*(first\s*)?name\b", "preferred_name"),
    (r"\be-?mail\b", "email"),
    (r"\bphone\b|\bmobile\b|\btelephone\b", "phone"),
    (r"\blinkedin\b", "linkedin"),
    (r"\bgithub\b", "github"),
    (r"\bresume\b|\bcurriculum\b|\bcv\b", "resume"),
    (r"\byears?\s+of\s+experience\b|\bexperience\s+in\s+years\b", "years_experience"),
    (r"\bcountry\b", "country"),
    (r"\bcity\b|\blocation\b|\baddress\b", "location"),
)
US_MARKERS = ("united states", "u.s.", "u.s.a.", " usa", " us ")


@dataclass(slots=True)
class FillResult:
    key: str
    label: str
    status: str


def profile_values(profile: ApplicantProfile) -> dict[str, str]:
    return {
        "first_name": profile.first_name,
        "last_name": profile.last_name,
        "preferred_name": profile.preferred_name,
        "email": profile.email,
        "phone": profile.phone,
        "linkedin": profile.linkedin,
        "github": profile.github,
        "location": profile.location,
        "country": profile.country,
        "resume": str(resolve_data_path(profile.resume_path)),
        "years_experience": str(profile.years_experience),
        "us_work_authorized": "Yes" if profile.us_work_authorized else "No",
        "sponsorship": "No" if profile.us_work_authorized else "Yes",
    }


def match_field(*texts: str | None) -> str | None:
    """Map a field label/name to a profile key."""
    haystack = " ".join(text.lower() for text in texts if text).strip()
    if not haystack:
        return None

    us_context = any(marker in f" {haystack} " for marker in US_MARKERS)
    if us_context:
        if "authoriz" in haystack or "right to work" in haystack or "eligible to work" in haystack:
            return "us_work_authorized"
        if "sponsor" in haystack:
            return "sponsorship"

    for pattern, key in RULES:
        if re.search(pattern, haystack):
            return key
    return None


def _element_label(element) -> str:
    try:
        return element.evaluate(
            """e => {
              const labels = e.labels ? Array.from(e.labels).map(l => l.innerText).join(' ') : '';
              const aria = e.getAttribute('aria-label') || '';
              const ph = e.getAttribute('placeholder') || '';
              const name = e.getAttribute('name') || '';
              const id = e.getAttribute('id') || '';
              return [labels, aria, ph, name, id].filter(Boolean).join(' | ');
            }"""
        )
    except Exception:  # noqa: BLE001
        return ""


def _near_text(element) -> str:
    try:
        return element.evaluate(
            """e => {
              const box = e.closest('div, fieldset, section, li, tr');
              return box ? box.innerText.slice(0, 180) : '';
            }"""
        )
    except Exception:  # noqa: BLE001
        return ""


def autofill(page, profile: ApplicantProfile) -> list[FillResult]:
    """Fill recognised fields on the current page; never submits."""
    values = profile_values(profile)
    results: list[FillResult] = []
    seen: set[str] = set()

    for element in page.query_selector_all("input, textarea, select"):
        try:
            if not element.is_visible():
                continue
            tag = (element.evaluate("e => e.tagName") or "").lower()
            field_type = (element.get_attribute("type") or "").lower()
            if field_type in {"hidden", "submit", "button", "reset", "image", "search"}:
                continue
            if tag == "input" and field_type in {"radio", "checkbox"}:
                continue

            label = _element_label(element)
            key = match_field(label)
            if key is None:
                key = match_field(label, _near_text(element))
            if key is None or key not in values or key in seen:
                continue
            value = values[key]
            if not value:
                continue

            if key == "resume":
                element.set_input_files(value)
                status = "uploaded"
            elif tag == "select":
                try:
                    element.select_option(label=value)
                except Exception:  # noqa: BLE001
                    element.select_option(value=value)
                status = "selected"
            else:
                element.fill(value)
                status = "filled"
            seen.add(key)
            results.append(FillResult(key=key, label=label[:120], status=status))
        except Exception as exc:  # noqa: BLE001
            results.append(
                FillResult(
                    key=key if "key" in locals() and key else "?",
                    label=label[:120] if "label" in locals() else "",
                    status=f"error: {type(exc).__name__}",
                )
            )

    return results


def unfilled_required(page) -> list[str]:
    """Return labels of visible required fields that are still empty."""
    try:
        return page.evaluate(
            """() => Array.from(document.querySelectorAll(
                 'input[required], select[required], textarea[required], [aria-required="true"]'))
              .filter(e => e.offsetParent !== null && !e.value)
              .map(e => (e.labels && e.labels[0] ? e.labels[0].innerText :
                         e.getAttribute('aria-label') || e.getAttribute('placeholder') || e.id || e.name || '?'))
              .slice(0, 30)"""
        )
    except Exception:  # noqa: BLE001
        return []


def summarize(results: list[FillResult]) -> dict[str, Any]:
    return {
        "filled": [r.key for r in results if r.status in {"filled", "selected", "uploaded"}],
        "errors": [r.key for r in results if r.status.startswith("error")],
    }
