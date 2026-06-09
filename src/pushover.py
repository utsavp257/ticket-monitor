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

import requests

from config import PUSHOVER_RETRY, PUSHOVER_EXPIRE


def _creds() -> tuple[str | None, str | None]:
    return os.environ.get("PUSHOVER_TOKEN"), os.environ.get("PUSHOVER_USER")


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
    data = {
        "token": token,
        "user": user,
        "message": message,
        "priority": 2,
        "retry": PUSHOVER_RETRY,
        "expire": expire,
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
