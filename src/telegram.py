"""Telegram notifications.

Reads credentials lazily so the rest of the program (e.g. a local --dry-run)
works even when TELEGRAM_TOKEN / TELEGRAM_CHAT_ID aren't set.
"""

from __future__ import annotations

import os

import requests


def _creds() -> tuple[str | None, str | None]:
    return os.environ.get("TELEGRAM_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")


def is_configured() -> bool:
    token, chat_id = _creds()
    return bool(token and chat_id)


def send_message(message: str) -> bool:
    """Send a Telegram message. Returns True on success, False otherwise.

    Never raises — a notification failure shouldn't crash the monitor.
    """
    token, chat_id = _creds()
    if not token or not chat_id:
        print("  ! Telegram not configured (set TELEGRAM_TOKEN / "
              "TELEGRAM_CHAT_ID); message not sent.")
        return False
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": message,
                "disable_web_page_preview": False,
            },
            timeout=15,
        )
    except requests.RequestException as e:
        print(f"  ! Telegram request failed: {e}")
        return False
    if resp.status_code != 200:
        # Telegram returns a JSON body explaining the failure (bad token,
        # wrong chat_id, bot never started, etc.) — surface it.
        print(f"  ! Telegram API error {resp.status_code}: {resp.text}")
        return False
    return True
