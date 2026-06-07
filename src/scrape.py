"""Fetch a theater page and pull *real* showtimes out of it.

This is the heart of the rewrite. The original code just checked whether the
word "Dune" appeared anywhere in the HTML — which is true even when there are
no tickets. Here we instead look for actual showtime data (a movie title tied
to a clock time on the requested date) and report the *earliest* one.

Two extraction strategies, tried in order:
  1. JSON-LD: many cinema sites embed <script type="application/ld+json"> with
     ScreeningEvent / Event objects that have a startDate. This is the most
     reliable when present.
  2. Text heuristic: scan the rendered visible text for the movie title and
     grab clock times (e.g. "10:30am") that appear near it.

Both are best-effort. Sites change; see README for how to debug selectors.
"""

from __future__ import annotations

import json
import re

from playwright.sync_api import sync_playwright

from config import PAGE_TIMEOUT_MS, SETTLE_MS, MAX_RETRIES

# A real-ish browser fingerprint. Headless Chromium with the default UA gets
# bot-challenged on AMC/Fandango far more often.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

TIME_RE = re.compile(r"\b(\d{1,2}):(\d{2})\s*([ap]\.?m\.?)", re.IGNORECASE)

# Pages that indicate we were blocked / served a challenge rather than content.
BLOCK_MARKERS = [
    "just a moment",
    "verify you are human",
    "are you a robot",
    "access denied",
    "enable javascript and cookies",
]


class Blocked(Exception):
    """Raised when the page looks like an anti-bot challenge, not content."""


def fetch(url: str, debug_dump: str | None = None) -> tuple[str, str]:
    """Return (html, visible_text) for `url`, retrying on transient failures.

    If `debug_dump` is set, the HTML is written there for inspection.
    Raises Blocked if every attempt looks like a bot challenge.
    """
    last_err: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            html, text = _fetch_once(url)
            lowered = text.lower()
            if any(m in lowered for m in BLOCK_MARKERS) and len(text) < 2000:
                raise Blocked(f"bot challenge on {url} (attempt {attempt})")
            if debug_dump:
                with open(debug_dump, "w", encoding="utf-8") as f:
                    f.write(html)
            return html, text
        except Blocked as e:
            last_err = e
            print(f"  ! {e}")
        except Exception as e:  # network, timeout, etc. — retry
            last_err = e
            print(f"  ! fetch error on {url} (attempt {attempt}): {e}")
    raise last_err if last_err else RuntimeError(f"failed to fetch {url}")


def _fetch_once(url: str) -> tuple[str, str]:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1366, "height": 900},
            locale="en-US",
        )
        page = context.new_page()
        try:
            # "networkidle" is unreliable on ad/analytics-heavy sites and is
            # discouraged by Playwright. Load the DOM, then give JS time to
            # render showtimes.
            page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
            page.wait_for_timeout(SETTLE_MS)
            html = page.content()
            text = page.inner_text("body")
            return html, text
        finally:
            browser.close()


def _to_minutes(hour: int, minute: int, meridiem: str) -> int:
    """Convert a 12-hour clock time to minutes past midnight, for sorting."""
    meridiem = meridiem.replace(".", "").lower()
    hour = hour % 12
    if meridiem == "pm":
        hour += 12
    return hour * 60 + minute


def _from_jsonld(html: str, aliases: list[str], date_iso: str) -> list[dict]:
    """Pull showtimes from JSON-LD ScreeningEvent/Event blocks."""
    found: list[dict] = []
    for block in re.findall(
        r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>',
        html,
        re.DOTALL | re.IGNORECASE,
    ):
        try:
            data = json.loads(block.strip())
        except json.JSONDecodeError:
            continue
        for node in _iter_nodes(data):
            if not isinstance(node, dict):
                continue
            name = str(node.get("name", "")).lower()
            start = str(node.get("startDate", ""))
            if not start or date_iso not in start:
                continue
            if not any(a in name for a in aliases):
                continue
            # startDate like 2026-06-09T19:30:00-04:00 -> grab HH:MM
            m = re.search(r"T(\d{2}):(\d{2})", start)
            if not m:
                continue
            minutes = int(m.group(1)) * 60 + int(m.group(2))
            found.append({"time": _fmt(minutes), "minutes": minutes})
    return found


def _from_text(text: str, aliases: list[str]) -> list[dict]:
    """Heuristic fallback: clock times appearing near the movie title."""
    lowered = text.lower()
    # Only proceed if the title is actually present.
    positions = [lowered.find(a) for a in aliases if a in lowered]
    if not positions:
        return []
    found: list[dict] = []
    # Look in a window of text following each title mention.
    for pos in positions:
        window = text[pos: pos + 1500]
        for m in TIME_RE.finditer(window):
            minutes = _to_minutes(int(m.group(1)), int(m.group(2)), m.group(3))
            found.append({"time": _fmt(minutes), "minutes": minutes})
    return found


def _iter_nodes(data):
    """Walk arbitrarily nested JSON-LD (lists, @graph, subEvent, etc.)."""
    if isinstance(data, list):
        for item in data:
            yield from _iter_nodes(item)
    elif isinstance(data, dict):
        yield data
        for key in ("@graph", "subEvent", "event", "workPresented"):
            if key in data:
                yield from _iter_nodes(data[key])


def _fmt(minutes: int) -> str:
    h, m = divmod(minutes, 60)
    suffix = "am" if h < 12 else "pm"
    hour12 = h % 12 or 12
    return f"{hour12}:{m:02d}{suffix}"


def find_earliest_show(
    html: str, text: str, aliases: list[str], date_iso: str
) -> dict | None:
    """Return the earliest showtime for a movie on a date, or None.

    Returns e.g. {"time": "10:30am", "minutes": 630, "shows": 4}.
    """
    shows = _from_jsonld(html, aliases, date_iso)
    if not shows:
        shows = _from_text(text, aliases)
    if not shows:
        return None
    earliest = min(shows, key=lambda s: s["minutes"])
    return {**earliest, "shows": len(shows)}
