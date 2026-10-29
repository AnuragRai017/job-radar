from __future__ import annotations

import html as html_module
import re

import bleach
from bs4 import BeautifulSoup

ALLOWED_TAGS = [
    "p",
    "br",
    "ul",
    "ol",
    "li",
    "strong",
    "b",
    "em",
    "i",
    "u",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "blockquote",
    "a",
    "div",
    "span",
    "table",
    "thead",
    "tbody",
    "tr",
    "th",
    "td",
    "hr",
    "pre",
    "code",
]
ALLOWED_ATTRIBUTES = {"a": ["href", "title"], "th": ["scope"], "td": ["colspan", "rowspan"]}
ALLOWED_PROTOCOLS = ["http", "https", "mailto"]

_HTML_HINT = re.compile(r"<\s*/?\s*[a-zA-Z][^>]*>")
_BLOCK_TAGS = {
    "address",
    "article",
    "aside",
    "blockquote",
    "dd",
    "div",
    "dl",
    "dt",
    "fieldset",
    "figcaption",
    "figure",
    "footer",
    "form",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "hr",
    "li",
    "main",
    "nav",
    "ol",
    "p",
    "pre",
    "section",
    "table",
    "td",
    "th",
    "tr",
    "ul",
}


def _linkify_external_links(cleaned: str) -> str:
    """Force safe, new-tab behaviour on links inside job descriptions."""
    if "<a" not in cleaned:
        return cleaned
    soup = BeautifulSoup(cleaned, "html.parser")
    for anchor in soup.find_all("a"):
        href = (anchor.get("href") or "").strip()
        if not href.lower().startswith(("http://", "https://", "mailto:")):
            anchor.unwrap()
            continue
        anchor["target"] = "_blank"
        anchor["rel"] = "noopener noreferrer nofollow"
    return str(soup)


def sanitize_html(raw_html: str) -> str:
    # Strip the content of active elements before bleach removes the tags;
    # bleach alone would keep script text as visible copy.
    soup = BeautifulSoup(raw_html or "", "html.parser")
    for node in soup(["script", "style", "iframe", "object", "embed", "template", "noscript"]):
        node.decompose()

    cleaned = bleach.clean(
        str(soup),
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        protocols=ALLOWED_PROTOCOLS,
        strip=True,
        strip_comments=True,
    )
    return _linkify_external_links(cleaned).strip()


def html_to_text(raw_html: str) -> str:
    if not raw_html:
        return ""
    soup = BeautifulSoup(raw_html, "html.parser")
    for node in soup(["script", "style"]):
        node.decompose()
    for br in soup.find_all("br"):
        br.replace_with("\n")
    # get_text() without a separator keeps inline markup glued together, while
    # appending whitespace to block elements preserves paragraph boundaries.
    for node in soup.find_all(_BLOCK_TAGS):
        node.append("\n\n")
    text = soup.get_text().replace("\xa0", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def text_to_html(raw_text: str) -> str:
    """Turn plain text from a JSON feed into safe paragraph/line markup."""
    if not raw_text:
        return ""
    normalized = raw_text.replace("\r\n", "\n").replace("\r", "\n").strip()
    paragraphs = re.split(r"\n\s*\n", normalized)
    rendered: list[str] = []
    for paragraph in paragraphs:
        lines = [html_module.escape(line.strip()) for line in paragraph.split("\n") if line.strip()]
        if lines:
            rendered.append("<p>" + "<br>".join(lines) + "</p>")
    return "\n".join(rendered)


def prepare_description(value: str) -> tuple[str, str]:
    """Return (sanitized_html, plain_text) for a source description."""
    raw = (value or "").strip()
    if not raw:
        return "", ""
    if _HTML_HINT.search(raw):
        cleaned = sanitize_html(raw)
        return cleaned, html_to_text(cleaned)
    cleaned = sanitize_html(text_to_html(raw))
    return cleaned, html_to_text(cleaned)


def make_excerpt(description_text: str, limit: int = 240) -> str:
    text = re.sub(r"\s+", " ", description_text or "").strip()
    if len(text) <= limit:
        return text
    truncated = text[:limit].rsplit(" ", 1)[0].rstrip(" ,.;:-")
    return truncated + "…"
