from __future__ import annotations

from app.sanitize import (
    html_to_text,
    make_excerpt,
    prepare_description,
    sanitize_html,
    text_to_html,
)
from app.utils import clean_text, epoch_to_date, parse_date, parse_datetime, safe_http_url


def test_sanitize_html_strips_active_content() -> None:
    raw = (
        '<p onclick="alert(1)">Hello <strong>world</strong></p>'
        "<script>alert('xss')</script>"
        '<a href="javascript:alert(1)">bad</a>'
        '<a href="https://example.com/role" onclick="x()">good</a>'
        "<iframe src=\"https://evil.example\"></iframe>"
    )
    cleaned = sanitize_html(raw)

    assert "onclick" not in cleaned
    assert "<script" not in cleaned
    assert "<iframe" not in cleaned
    assert "alert('xss')" not in cleaned
    assert "javascript:" not in cleaned
    assert "<strong>world</strong>" in cleaned
    assert 'href="https://example.com/role"' in cleaned
    assert 'target="_blank"' in cleaned
    assert "noopener noreferrer nofollow" in cleaned


def test_text_to_html_escapes_markup_and_preserves_paragraphs() -> None:
    rendered = text_to_html("First line\nsecond line\n\nA <script> tag & more")
    assert rendered == (
        "<p>First line<br>second line</p>\n"
        "<p>A &lt;script&gt; tag &amp; more</p>"
    )


def test_prepare_description_detects_html_versus_plain_text() -> None:
    html, text = prepare_description("<p>Build <em>things</em>.</p>")
    assert html == "<p>Build <em>things</em>.</p>"
    assert text == "Build things."

    html, text = prepare_description("Plain text\n\nSecond paragraph")
    assert html == "<p>Plain text</p>\n<p>Second paragraph</p>"
    assert text == "Plain text\n\nSecond paragraph"


def test_html_to_text_ignores_script_and_style_content() -> None:
    assert html_to_text("<style>.x{}</style><p>Hello<script>bad()</script> world</p>") == "Hello world"


def test_make_excerpt_collapses_whitespace_and_breaks_on_a_word() -> None:
    text = "word " * 80
    excerpt = make_excerpt(text, limit=20)
    assert excerpt.endswith("…")
    assert len(excerpt) <= 21
    assert "  " not in excerpt

def test_clean_text_and_parsers() -> None:
    assert clean_text("  a\n\tb  ") == "a b"
    assert clean_text("abcdef", max_length=4) == "abc…"
    assert parse_datetime("2025-01-02T03:04:05Z").isoformat() == "2025-01-02T03:04:05+00:00"
    assert parse_date("2025-01-02").isoformat() == "2025-01-02"
    assert parse_date("not-a-date") is None
    assert epoch_to_date([1_735_689_600, 0]).isoformat() == "2025-01-01"
    assert epoch_to_date(None) is None


def test_safe_http_url_only_allows_http_and_https() -> None:
    assert safe_http_url("https://example.com/job") == "https://example.com/job"
    assert safe_http_url("http://example.com/job") == "http://example.com/job"
    assert safe_http_url("javascript:alert(1)", "https://fallback.test") == "https://fallback.test"
    assert safe_http_url("data:text/html,hello", "https://fallback.test") == "https://fallback.test"
    assert safe_http_url("", "https://fallback.test") == "https://fallback.test"
