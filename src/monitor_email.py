"""Watch a Gmail mailbox for Warner Bros "Dune Insider" emails.

Warner Bros sends the Dune Insider newsletter — and, we expect, ticket on-sale
announcements — from warnerbros@updates.warnerbros.com. We read the mailbox over
IMAP: free, stdlib-only (imaplib + email), no third party and no quotas, using a
Gmail App Password in GMAIL_USER / GMAIL_APP_PASSWORD. Unlike the Apify-backed
Instagram check there's nothing to ration, so we check every run for near
real-time coverage of a ticket drop.

Two things happen per new matching email (see main.email_diff_and_alert):
  1. It's forwarded to Telegram in full — the definitive safety net; you always
     see the whole email regardless of any parsing.
  2. Its text is scanned for on-sale keywords (config.EMAIL_ONSALE_PHRASES); a
     hit also fires a Pushover siren. Because (1) always fires, this keyword
     layer only decides escalation — a missed phrase still reaches you, and a
     false hit is just one extra siren. So we bias toward recall.
"""

from __future__ import annotations

import email
import html
import imaplib
import os
import re
from datetime import date, timedelta
from email.header import decode_header, make_header
from email.message import Message
from email.utils import parseaddr

from config import (EMAIL_IMAP_HOST, EMAIL_FOLDER, EMAIL_SENDER,
                    EMAIL_TEXT_MATCH, EMAIL_SINCE_DAYS, EMAIL_MAX_FETCH,
                    EMAIL_ONSALE_PHRASES)


def _creds() -> tuple[str | None, str | None]:
    return os.environ.get("GMAIL_USER"), os.environ.get("GMAIL_APP_PASSWORD")


def is_configured() -> bool:
    user, pw = _creds()
    return bool(user and pw)


def _decode(value: str | None) -> str:
    """Decode a possibly RFC 2047-encoded header (e.g. =?utf-8?q?...?=)."""
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


_TAG_RE = re.compile(r"<[^>]+>")


def _html_to_text(raw: str) -> str:
    """Cheap HTML→text: drop script/style, turn common block tags into
    newlines, strip the rest. Good enough for keyword scans and a readable
    Telegram preview without pulling in an HTML-parser dependency."""
    raw = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", raw)
    raw = re.sub(r"(?i)<br\s*/?>", "\n", raw)
    raw = re.sub(r"(?i)</(p|div|tr|li|h[1-6]|table)>", "\n", raw)
    text = _TAG_RE.sub(" ", raw)
    text = html.unescape(text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _part_text(part: Message) -> str:
    try:
        payload = part.get_payload(decode=True)
        if payload is None:
            return ""
        charset = part.get_content_charset() or "utf-8"
        return payload.decode(charset, errors="replace")
    except Exception:
        return ""


def _body_text(msg: Message) -> str:
    """Extract readable text: prefer text/plain parts, fall back to stripped
    text/html. Skips attachments."""
    plain: list[str] = []
    htmls: list[str] = []
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_maintype() == "multipart":
                continue
            disp = str(part.get("Content-Disposition") or "")
            if disp.lower().startswith("attachment"):
                continue
            ctype = part.get_content_type()
            if ctype == "text/plain":
                plain.append(_part_text(part))
            elif ctype == "text/html":
                htmls.append(_part_text(part))
    elif msg.get_content_type() == "text/html":
        htmls.append(_part_text(msg))
    else:
        plain.append(_part_text(msg))

    if any(p.strip() for p in plain):
        return "\n".join(p for p in plain if p.strip()).strip()
    return _html_to_text("\n".join(htmls))


def onsale_hits(text: str) -> list[str]:
    """On-sale phrases (config.EMAIL_ONSALE_PHRASES) present in the text,
    matched case-insensitively, in config order."""
    low = text.lower()
    return [p for p in EMAIL_ONSALE_PHRASES if p.lower() in low]


def check_email() -> list[dict]:
    """Return recent matching emails as
    [{msg_id, from, subject, date, body, onsale}], newest first.

    Matches mail from EMAIL_SENDER OR containing EMAIL_TEXT_MATCH, within the
    last EMAIL_SINCE_DAYS days. Connection/auth/search errors propagate so the
    caller can log them (like the other monitors); an empty mailbox is []."""
    user, pw = _creds()
    if not (user and pw):
        return []

    conn = imaplib.IMAP4_SSL(EMAIL_IMAP_HOST)
    try:
        conn.login(user, pw)
        # readonly: never mark WB's mail read/flagged just by monitoring it.
        conn.select(EMAIL_FOLDER, readonly=True)

        since = (date.today() - timedelta(days=EMAIL_SINCE_DAYS)).strftime("%d-%b-%Y")
        # Standard IMAP criteria: SINCE <date> AND (FROM sender OR TEXT phrase).
        # Prefix OR binds the next two keys; the two top-level keys are ANDed.
        # Phrase/args are pre-quoted because imaplib sends args verbatim (it
        # won't quote a value containing a space for us).
        typ, data = conn.uid(
            "SEARCH", "SINCE", since,
            "OR", "FROM", f'"{EMAIL_SENDER}"', "TEXT", f'"{EMAIL_TEXT_MATCH}"',
        )
        if typ != "OK":
            raise RuntimeError(f"IMAP SEARCH failed: {typ} {data}")

        uids = data[0].split()
        uids = uids[-EMAIL_MAX_FETCH:]  # cap: newest N, so a big backlog is bounded
        out: list[dict] = []
        for uid in reversed(uids):  # newest first
            typ, msg_data = conn.uid("FETCH", uid, "(RFC822)")
            if typ != "OK" or not msg_data or not isinstance(msg_data[0], tuple):
                continue
            msg = email.message_from_bytes(msg_data[0][1])
            msg_id = (msg.get("Message-ID") or f"uid:{uid.decode()}").strip()
            subject = _decode(msg.get("Subject"))
            frm = parseaddr(_decode(msg.get("From")))[1]
            body = _body_text(msg)
            out.append({
                "msg_id": msg_id,
                "from": frm,
                "subject": subject,
                "date": _decode(msg.get("Date")),
                "body": body,
                "onsale": onsale_hits(f"{subject}\n{body}"),
            })
        return out
    finally:
        try:
            conn.logout()
        except Exception:
            pass
