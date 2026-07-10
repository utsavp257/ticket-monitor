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
# Invisible spacer/zero-width/combining chars that marketing preheaders pad
# with (figure space, ZWJ/ZWNJ, BOM, combining marks). Strip runs of them so
# the preview and keyword scan see real words, not decorative filler.
_JUNK_RE = re.compile(
    "[\u200b-\u200f\u202a-\u202e\u2007\u202f\u2060\ufeff\u0300-\u036f]+")


def _clean_text(raw: str) -> str:
    """Normalize a text or HTML fragment to readable plain text. Drops
    script/style, turns block tags into newlines, strips remaining tags,
    unescapes entities and removes invisible filler. Safe on real plain text
    too (WB's text/plain part is itself full of tags/junk), so we run every
    part through it rather than trusting the Content-Type."""
    raw = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", raw)
    raw = re.sub(r"(?i)<br\s*/?>", "\n", raw)
    raw = re.sub(r"(?i)</(p|div|tr|li|h[1-6]|table)>", "\n", raw)
    text = _TAG_RE.sub(" ", raw)
    text = html.unescape(text)
    text = _JUNK_RE.sub("", text)
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


def _letters(s: str) -> int:
    return sum(c.isalpha() for c in s)


def _extract_text(msg: Message) -> tuple[str, str]:
    """Return (preview, scan_text). `preview` is the more substantive of the
    cleaned text/plain vs text/html alternatives (marketing mail often ships a
    junk plain part, so we can't just prefer plain). `scan_text` is BOTH parts
    combined — keyword detection reads everything so an on-sale phrase that
    lives only in the HTML part is never missed. Attachments are skipped."""
    plains: list[str] = []
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
                plains.append(_part_text(part))
            elif ctype == "text/html":
                htmls.append(_part_text(part))
    elif msg.get_content_type() == "text/html":
        htmls.append(_part_text(msg))
    else:
        plains.append(_part_text(msg))

    plain_clean = _clean_text("\n".join(plains))
    html_clean = _clean_text("\n".join(htmls))
    preview = (plain_clean if _letters(plain_clean) >= _letters(html_clean)
               else html_clean)
    scan_text = f"{plain_clean}\n{html_clean}"
    return preview, scan_text


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
            preview, scan_text = _extract_text(msg)
            out.append({
                "msg_id": msg_id,
                "from": frm,
                "subject": subject,
                "date": _decode(msg.get("Date")),
                "body": preview,
                # scan BOTH parts (subject + plain + html) so a phrase living
                # only in the HTML alternative is still caught.
                "onsale": onsale_hits(f"{subject}\n{scan_text}"),
            })
        return out
    finally:
        try:
            conn.logout()
        except Exception:
            pass
