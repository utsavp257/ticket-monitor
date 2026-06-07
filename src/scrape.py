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
import time

from playwright.sync_api import sync_playwright

from config import PAGE_TIMEOUT_MS, SETTLE_MS, MAX_RETRIES

# A real-ish browser fingerprint. Headless Chromium with the default UA gets
# bot-challenged by AMC far more often.
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
    "verify you are not a bot",
    "performing security verification",
    "security service to protect",
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
            print(f"  ! fetch error on {url} (attempt {attempt}): "
                  f"{str(e).splitlines()[0]}")
        if attempt < MAX_RETRIES:
            time.sleep(5 * attempt)  # back off; gives throttling a moment
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
            # "commit" returns as soon as the server responds, rather than
            # waiting for DOMContentLoaded — which can hang (and time out) when
            # an anti-bot layer slow-walks the connection (e.g. AMC from
            # datacenter IPs). "networkidle" is even worse on ad-heavy sites.
            page.goto(url, wait_until="commit", timeout=PAGE_TIMEOUT_MS)
            # Poll until showtimes have actually rendered (a clock time appears)
            # instead of a fixed sleep — faster on the happy path, and gives
            # slow renders room without a hard fail.
            deadline = SETTLE_MS + 12_000
            waited = 0
            while waited < deadline:
                page.wait_for_timeout(1000)
                waited += 1000
                if waited >= SETTLE_MS and TIME_RE.search(page.inner_text("body")):
                    break
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


# A movie's real listing on AMC (and similar sites) is anchored by a runtime
# line right under the title, e.g. "2 HR 20 MIN" or "1 HR 36 MIN". The same
# title also appears in a filter/nav list with NO runtime under it — which is
# why a naive "title then nearby times" search binds the wrong showtimes.
RUNTIME_RE = re.compile(r"^\d+\s*HR(\s+\d+\s*MIN)?$", re.IGNORECASE)


def _segment_blocks(text: str) -> list[tuple[str, str]]:
    """Split rendered text into (title, block_text) per movie listing.

    A title is a non-empty line whose next non-empty line is a runtime
    ("2 HR 20 MIN"). Each block runs until the next such anchor, so the times
    inside a block belong to that movie only.
    """
    lines = [ln.strip() for ln in text.splitlines()]

    def next_nonempty(i: int) -> int:
        j = i + 1
        while j < len(lines) and not lines[j]:
            j += 1
        return j

    anchors = [
        i for i, ln in enumerate(lines)
        if ln and (j := next_nonempty(i)) < len(lines) and RUNTIME_RE.match(lines[j])
    ]
    blocks: list[tuple[str, str]] = []
    for idx, start in enumerate(anchors):
        end = anchors[idx + 1] if idx + 1 < len(anchors) else len(lines)
        blocks.append((lines[start], "\n".join(lines[start:end])))
    return blocks


def _from_blocks(text: str, aliases: list[str]) -> list[dict]:
    """Times scoped to the matching movie's listing block (the reliable path
    for AMC-style pages)."""
    found: list[dict] = []
    for title, body in _segment_blocks(text):
        if not any(a in title.lower() for a in aliases):
            continue
        for m in TIME_RE.finditer(body):
            minutes = _to_minutes(int(m.group(1)), int(m.group(2)), m.group(3))
            found.append({"time": _fmt(minutes), "minutes": minutes})
    return found


def _from_text_window(text: str, aliases: list[str]) -> list[dict]:
    """Last-resort heuristic for pages with no runtime-anchored listings:
    clock times in a window after the title mention."""
    lowered = text.lower()
    positions = [lowered.find(a) for a in aliases if a in lowered]
    if not positions:
        return []
    found: list[dict] = []
    for pos in positions:
        window = text[pos: pos + 1200]
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
    # 1) JSON-LD if present (some sites embed ScreeningEvents).
    shows = _from_jsonld(html, aliases, date_iso)
    # 2) Per-movie blocks anchored on the runtime line (AMC-style — reliable).
    if not shows:
        shows = _from_blocks(text, aliases)
    # 3) Window heuristic ONLY when the page has no anchored listings at all,
    #    so it can't clobber correct block parsing with mis-bound times.
    if not shows and not _segment_blocks(text):
        shows = _from_text_window(text, aliases)
    if not shows:
        return None
    earliest = min(shows, key=lambda s: s["minutes"])
    return {**earliest, "shows": len(shows)}
