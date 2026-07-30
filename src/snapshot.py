"""Persist each run's full AMC API pull so its git history is a timeline.

Every API check writes the complete pull (every showtime AMC returned for the
watched dates — all movies, all formats) to two files in state/:

  snapshot.json — the full data, trimmed to stable fields
  SNAPSHOT.md   — the same, as human-readable tables

Both are deterministic (sorted, volatile fields dropped), so the workflow's
state commit only picks them up when the DATA changed — new showtimes, a
sold-out flip, a listing pulled. Browsing the file's history on GitHub (or
`git log -p state/SNAPSHOT.md`) then shows exactly what AMC returned and when
it changed, with the commit time as the pull time.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

_STATE_DIR = Path(__file__).resolve().parent.parent / "state"
SNAP_JSON = _STATE_DIR / "snapshot.json"
SNAP_MD = _STATE_DIR / "SNAPSHOT.md"


def _fmt_time(local: str) -> str:
    """'2026-08-04T19:00:00' -> '7:00pm' (also used as the sort key source)."""
    hh, mm = local.split("T")[1].split(":")[:2]
    h, m = int(hh), int(mm)
    return f"{h % 12 or 12}:{m:02d}{'am' if h < 12 else 'pm'}"


def _minutes(local: str) -> int:
    hh, mm = local.split("T")[1].split(":")[:2]
    return int(hh) * 60 + int(mm)


def _trim(s: dict) -> dict:
    """The fields worth keeping, chosen to be stable run-to-run (volatile
    fields like lastUpdatedDateUtc would turn every run into a commit)."""
    return {
        "id": s.get("id"),
        "movie": s.get("movieName"),
        "time": _fmt_time(s["showDateTimeLocal"]) if "T" in str(
            s.get("showDateTimeLocal", "")) else "",
        "format": s.get("premiumFormat") or "standard",
        "soldOut": bool(s.get("isSoldOut")),
        "almostSoldOut": bool(s.get("isAlmostSoldOut")),
        "canceled": bool(s.get("isCanceled")),
        "attributes": sorted(a.get("code", "") for a in s.get("attributes") or []),
    }


def _status(s: dict) -> str:
    if s["canceled"]:
        return "🚫 canceled"
    if s["soldOut"]:
        return "🔴 SOLD OUT"
    if s["almostSoldOut"]:
        return "🟠 almost sold out"
    return "🟢 available"


def write(pulls: dict) -> None:
    """pulls: {date_iso: [raw showtime dicts from the API]}. Best-effort —
    callers guard with try/except; never let this break the monitor."""
    data = {
        iso: sorted((_trim(s) for s in showtimes),
                    key=lambda t: (t["movie"] or "", _minutes_of(t), t["id"] or 0))
        for iso, showtimes in sorted(pulls.items())
    }

    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(SNAP_JSON, "w") as f:
        json.dump(data, f, indent=1, sort_keys=True)
        f.write("\n")

    lines = [
        "# AMC Lincoln Square 13 — latest API pull",
        "",
        "Everything AMC's API returned for the watched dates (all movies, all",
        "formats), refreshed every poll but committed **only when it changes** —",
        "so this file's git history is the timeline of listings/sell-outs, and",
        "each commit's timestamp is the pull time. Browse it on GitHub via this",
        "file's *History*, or locally with `git log -p state/SNAPSHOT.md`.",
        "Machine-readable version: `state/snapshot.json`.",
    ]
    for iso, shows in data.items():
        wd = date.fromisoformat(iso).strftime("%A")
        lines += ["", f"## {wd} {iso}", ""]
        if not shows:
            lines.append("_No showtimes listed._")
            continue
        lines += ["| Movie | Time | Format | Status | IMAX | Showtime id |",
                  "|---|---|---|---|---|---|"]
        for s in shows:
            imax = "🎬" if any("imax" in a.lower() for a in s["attributes"]) \
                or "imax" in s["format"].lower() else ""
            lines.append(f"| {s['movie']} | {s['time']} | {s['format']} "
                         f"| {_status(s)} | {imax} | {s['id']} |")
    with open(SNAP_MD, "w") as f:
        f.write("\n".join(lines) + "\n")


def _minutes_of(trimmed: dict) -> int:
    t = trimmed["time"]
    if not t:
        return 0
    h, rest = t[:-2].split(":")
    h, m = int(h), int(rest)
    if t.endswith("pm") and h != 12:
        h += 12
    if t.endswith("am") and h == 12:
        h = 0
    return h * 60 + m
