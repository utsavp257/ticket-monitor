"""Comprehensive self-diagnostic for the ticket monitor.

Verifies the whole pipeline end to end and reports what's healthy vs broken:
  - Telegram credentials valid (so alerts can actually be delivered)
  - Pushover configured (so escalation isn't silently skipped)
  - State file readable
  - AMC actually returning DATA: pages reachable, real movie listings parsed,
    real showtimes parsed, and each watched movie found on >=1 watched date
  - Instagram reachable (best-effort; not a hard failure)

Hard failures (things that mean the monitor is blind) are collected and, unless
this is a dry check, sent to Telegram. Warnings are surfaced in the report but
don't trigger an alert on their own.
"""

from __future__ import annotations

import os
import re
from datetime import date

import requests

import telegram
import pushover
import amc_api
from config import MOVIES, AMC_THEATRE_ID, amc_url
from dates import movie_watch_dates
from scrape import fetch, find_shows, count_listings
from state import load_state
from datetime import timedelta

TIME_TOKEN = re.compile(r"\b\d{1,2}:\d{2}\s*[ap]\.?m\.?", re.IGNORECASE)


def _telegram_token_ok() -> tuple[bool, str]:
    token = os.environ.get("TELEGRAM_TOKEN")
    if not token:
        return False, "TELEGRAM_TOKEN not set"
    try:
        r = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=10)
        if r.status_code == 200 and r.json().get("ok"):
            return True, "valid"
        return False, f"getMe returned {r.status_code}"
    except requests.RequestException as e:
        return False, str(e)


def run() -> tuple[list[str], list[str], list[str]]:
    """Return (report_lines, failures, warnings)."""
    report: list[str] = []
    failures: list[str] = []
    warnings: list[str] = []

    # --- Notification channels ---
    ok, msg = _telegram_token_ok()
    report.append(f"Telegram token: {'OK' if ok else 'FAIL — ' + msg}")
    if not ok:
        failures.append(f"Telegram token invalid/missing ({msg})")
    if os.environ.get("TELEGRAM_CHAT_ID"):
        report.append("Telegram chat_id: set")
    else:
        report.append("Telegram chat_id: MISSING")
        failures.append("TELEGRAM_CHAT_ID not set — alerts can't be delivered")
    report.append(f"Pushover: {'configured' if pushover.is_configured() else 'NOT configured'}")
    if not pushover.is_configured():
        warnings.append("Pushover creds missing — escalation siren would be skipped")

    # --- State ---
    try:
        st = load_state()
        report.append(f"State: readable ({len(st.get('shows', {}))} show keys, "
                      f"streak={st.get('amc_fail_streak', 0)})")
    except Exception as e:
        report.append("State: UNREADABLE")
        failures.append(f"State file unreadable: {e}")

    # --- AMC: are we actually getting data? ---
    today = date.today()
    dates = sorted({d.isoformat()
                    for spec in MOVIES.values()
                    for d in movie_watch_dates(spec, today)})
    per_movie = {m: 0 for m in MOVIES}

    if amc_api.is_configured():
        # Official API path. A near date proves the API works (theatre is open),
        # since far-future watched dates legitimately have no showtimes.
        try:
            tid = AMC_THEATRE_ID or amc_api.resolve_theatre_id()[0]
            near = (today + timedelta(days=2)).isoformat()
            near_count = len(amc_api.iter_showtimes(tid, near))
            report.append(f"AMC API: OK (theatre {tid}, {near_count} showtimes "
                          f"on {near})")
            if near_count == 0:
                failures.append("AMC API returned 0 showtimes for a near date "
                                "— key or endpoint problem")
            for iso in dates:
                showtimes = amc_api.iter_showtimes(tid, iso)
                for movie, spec in MOVIES.items():
                    aliases = spec["aliases"] if isinstance(spec, dict) else spec
                    if amc_api.match_shows(showtimes, aliases, False):
                        per_movie[movie] += 1
        except Exception as e:
            failures.append(f"AMC API failed: {str(e).splitlines()[0]}")
    else:
        # Fallback: scrape the consumer site (subject to IP blocking).
        fetched = 0
        total_listings = 0
        for iso in dates:
            try:
                _html, text = fetch(amc_url(iso))
            except Exception as e:
                report.append(f"  AMC {iso}: fetch FAIL ({str(e).splitlines()[0]})")
                continue
            fetched += 1
            total_listings += count_listings(text)
            for movie, spec in MOVIES.items():
                aliases = spec["aliases"] if isinstance(spec, dict) else spec
                if find_shows(text, aliases):
                    per_movie[movie] += 1
        report.append(f"AMC (scrape): fetched {fetched}/{len(dates)} dates | "
                      f"{total_listings} listings")
        if dates and fetched == 0:
            failures.append("AMC unreachable — every page fetch failed")
        elif fetched and total_listings == 0:
            failures.append("AMC returned zero listings — IP blocked or layout changed")

    for movie, count in per_movie.items():
        report.append(f"  {movie}: found on {count}/{len(dates)} watched dates")
        if not failures and count == 0:
            warnings.append(f"{movie} not found on any watched date "
                            "(off-sale, or its title/alias changed?)")

    # --- Instagram (best-effort; never a hard failure) ---
    try:
        from monitor_instagram import check_instagram
        posts = check_instagram()
        if posts:
            report.append(f"Instagram: OK ({len(posts)} posts retrieved)")
        else:
            report.append("Instagram: no posts (often IP-blocked — best-effort)")
            warnings.append("Instagram returned no posts (expected on CI at times)")
    except Exception as e:
        report.append(f"Instagram: error ({str(e).splitlines()[0]})")
        warnings.append(f"Instagram error: {str(e).splitlines()[0]}")

    return report, failures, warnings


def run_and_notify(announce: bool = False) -> bool:
    """Run the check, print the report, Telegram on failure (or always if
    announce). Returns True if healthy."""
    report, failures, warnings = run()
    print("=== HEALTH CHECK ===")
    print("\n".join(report))
    print("\nFAILURES:", "; ".join(failures) if failures else "none")
    print("WARNINGS:", "; ".join(warnings) if warnings else "none")

    healthy = not failures
    body = "\n".join(report)
    if failures:
        telegram.send_message(
            "🩺 Ticket monitor HEALTH CHECK FAILED\n\n"
            + "\n".join("• " + f for f in failures)
            + "\n\n--- full report ---\n" + body)
    elif announce:
        note = ("\n\nwarnings:\n" + "\n".join("• " + w for w in warnings)) if warnings else ""
        telegram.send_message("🩺 Health check OK — everything's working.\n\n"
                              + body + note)
    return healthy
