from __future__ import annotations

import email
import imaplib
import re
import time
from datetime import UTC, datetime
from email.message import Message
from email.utils import parsedate_to_datetime

CODE_PATTERNS = (
    # Medallion/Greenhouse sends an 8-digit numeric code. Match the number that
    # follows the "security code" instructions; the surrounding text also
    # contains 8-letter words such as "security" and "resubmit".
    re.compile(r"(?:security|verification)\s+code[\s\S]{0,160}?\b(\d{8})\b", re.I),
    re.compile(r"\b(\d{8})\b"),
)
SENDER_MARKERS = ("greenhouse", "medallion", "job-boards")


def _decode(value: str | None) -> str:
    if not value:
        return ""
    parts = []
    for chunk, charset in email.header.decode_header(value):
        if isinstance(chunk, bytes):
            parts.append(chunk.decode(charset or "utf-8", errors="replace"))
        else:
            parts.append(chunk)
    return "".join(parts)


def _body_text(message: Message) -> str:
    if message.is_multipart():
        for part in message.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    return payload.decode(part.get_content_charset() or "utf-8", "replace")
        for part in message.walk():
            if part.get_content_type() == "text/html":
                payload = part.get_payload(decode=True)
                if payload:
                    html = payload.decode(part.get_content_charset() or "utf-8", "replace")
                    return re.sub(r"<[^>]+>", " ", html)
        return ""
    payload = message.get_payload(decode=True)
    if payload:
        return payload.decode(message.get_content_charset() or "utf-8", "replace")
    return str(message.get_payload() or "")


def extract_code(text: str) -> str | None:
    for pattern in CODE_PATTERNS:
        match = pattern.search(text or "")
        if match:
            code = match.group(1).upper()
            if code.isalnum() and len(code) == 8:
                return code
    return None


def _check_once(
    *,
    host: str,
    user: str,
    password: str,
    folder: str,
    since: datetime,
) -> str | None:
    # Google displays app passwords in groups of four; IMAP expects the
    # 16-character value without spaces.
    password = password.replace(" ", "")
    with imaplib.IMAP4_SSL(host) as mail:
        mail.login(user, password)
        mail.select(folder)
        day = since.strftime("%d-%b-%Y")
        status, data = mail.search(None, f'(SINCE "{day}")')
        if status != "OK" or not data or not data[0]:
            return None
        message_ids = data[0].split()[-30:]
        best: tuple[datetime, str] | None = None
        for message_id in reversed(message_ids):
            status, payload = mail.fetch(message_id, "(RFC822)")
            if status != "OK" or not payload or not isinstance(payload[0], tuple):
                continue
            message = email.message_from_bytes(payload[0][1])
            try:
                received = parsedate_to_datetime(message.get("Date"))
            except (TypeError, ValueError):
                received = None
            if received is not None:
                if received.tzinfo is None:
                    received = received.replace(tzinfo=UTC)
                if received < since:
                    continue
            sender = _decode(message.get("From")).lower()
            if not any(marker in sender for marker in SENDER_MARKERS):
                continue
            code = extract_code(_body_text(message))
            if not code:
                continue
            stamp = received or datetime.min.replace(tzinfo=UTC)
            if best is None or stamp > best[0]:
                best = (stamp, code)
        return best[1] if best else None
    return None


def fetch_code(
    since: datetime,
    *,
    host: str,
    user: str,
    password: str,
    folder: str = "INBOX",
    timeout: float = 120.0,
    poll: float = 5.0,
) -> str | None:
    """Poll an IMAP inbox until a Greenhouse verification code arrives."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            code = _check_once(
                host=host, user=user, password=password, folder=folder, since=since
            )
        except (imaplib.IMAP4.error, OSError) as exc:
            print(f"IMAP error: {exc}")
            code = None
        if code:
            return code
        time.sleep(poll)
    return None
