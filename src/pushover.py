"""Pushover "emergency" escalation — the can't-miss alert.

Priority 2 makes Pushover re-siren every PUSHOVER_RETRY seconds (overriding
silent/Do-Not-Disturb) until you acknowledge in the app, for up to
PUSHOVER_EXPIRE seconds. Crucially, the repeat-until-acknowledged happens on
Pushover's servers — we just send one message, so it keeps nagging even after
the GitHub run ends.

Credentials come from PUSHOVER_TOKEN (app/API token) and PUSHOVER_USER (your
user key). If they're absent it no-ops gracefully (so local/dry runs are fine).
"""

from __future__ import annotations

import os
from datetime import datetime

import requests

from config import (PUSHOVER_RETRY, PUSHOVER_EXPIRE, PUSHOVER_SOUND,
                    SIREN_BLACKOUTS, SIREN_BLACKOUT_TZ)


def _creds() -> tuple[str | None, str | None]:
    return os.environ.get("PUSHOVER_TOKEN"), os.environ.get("PUSHOVER_USER")


def _active_blackout() -> str | None:
    """Description of the blackout window we're inside, or None.

    Fails OPEN on any problem (bad tz database, malformed window): a spurious
    siren is a nuisance, a silently swallowed one loses the ticket. So anything
    we can't evaluate is treated as "not in a blackout".
    """
    if not SIREN_BLACKOUTS:
        return None
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(SIREN_BLACKOUT_TZ)
        now = datetime.now(tz)
    except Exception as e:  # no tzdata, bad zone name — don't gag the siren
        print(f"  ! blackout check skipped ({e}); siren allowed.")
        return None
    for window in SIREN_BLACKOUTS:
        try:
            start, end = window
            # ZoneInfo resolves the UTC offset from the datetime itself, so
            # attaching it via replace() gives the right EDT/EST offset.
            s = datetime.strptime(start, "%Y-%m-%d %H:%M").replace(tzinfo=tz)
            e = datetime.strptime(end, "%Y-%m-%d %H:%M").replace(tzinfo=tz)
        except (ValueError, TypeError) as exc:
            print(f"  ! ignoring malformed SIREN_BLACKOUTS entry {window!r}: {exc}")
            continue
        if s <= now < e:
            return f"{start} → {end} ({SIREN_BLACKOUT_TZ})"
    return None


def is_configured() -> bool:
    token, user = _creds()
    return bool(token and user)


def send_emergency(
    message: str,
    title: str | None = None,
    url: str | None = None,
    url_title: str | None = None,
    expire: int = PUSHOVER_EXPIRE,
) -> bool:
    """Send a priority-2 (emergency) Pushover alert. Returns True on success.

    Never raises — escalation failing shouldn't crash the monitor.
    """
    token, user = _creds()
    if not token or not user:
        print("  ! Pushover not configured (PUSHOVER_TOKEN / PUSHOVER_USER); "
              "not escalated.")
        return False
    blackout = _active_blackout()
    if blackout:
        print(f"  ⏸ siren suppressed — blackout {blackout}. "
              f"Telegram still sent; state/arming unaffected.")
        return False
    data = {
        "token": token,
        "user": user,
        "message": message,
        "priority": 2,
        "retry": PUSHOVER_RETRY,
        "expire": expire,
        "sound": PUSHOVER_SOUND,
    }
    if title:
        data["title"] = title
    if url:
        data["url"] = url
    if url_title:
        data["url_title"] = url_title
    try:
        resp = requests.post(
            "https://api.pushover.net/1/messages.json", data=data, timeout=15
        )
    except requests.RequestException as e:
        print(f"  ! Pushover request failed: {e}")
        return False
    if resp.status_code != 200:
        print(f"  ! Pushover API error {resp.status_code}: {resp.text}")
        return False
    return True
