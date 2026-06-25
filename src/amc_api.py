"""AMC official catalog API client — the reliable replacement for scraping.

Uses api.amctheatres.com (vendor-key auth). It returns lightweight JSON and is
NOT behind the consumer site's Cloudflare/Turnstile wall, so it doesn't block
our datacenter IP — which is what lets us poll every 5 minutes safely.

Set AMC_VENDOR_KEY in the environment. When it's absent, monitor_amc falls back
to the old HTML scraper.
"""

from __future__ import annotations

import os

import requests

from config import AMC_THEATRE_SLUG
from scrape import _to_minutes, _fmt  # reuse time helpers

API = "https://api.amctheatres.com/v2"


def is_configured() -> bool:
    return bool(os.environ.get("AMC_VENDOR_KEY"))


def _headers() -> dict:
    return {
        "X-AMC-Vendor-Key": os.environ.get("AMC_VENDOR_KEY", ""),
        "Accept": "application/json",
    }


def _get(path: str, params: dict | None = None) -> dict:
    r = requests.get(f"{API}/{path.lstrip('/')}", params=params,
                     headers=_headers(), timeout=25)
    if r.status_code != 200:
        raise RuntimeError(f"AMC API {r.status_code} on {path}: {r.text[:200]}")
    return r.json()


def ping() -> bool:
    """One cheap authed call — True if the vendor key is authorized."""
    try:
        _get("theatres", {"page-size": 1})
        return True
    except Exception:
        return False


def resolve_theatre_id() -> tuple[int, str]:
    """Find AMC Lincoln Square 13's numeric id by its slug.

    Tries a direct slug lookup first, then pages the theatre list.
    """
    # Direct lookup (some deployments accept the slug as the id segment).
    try:
        data = _get(f"theatres/{AMC_THEATRE_SLUG}")
        if data.get("id"):
            return int(data["id"]), data.get("name", "")
    except Exception:
        pass
    # Page through the theatre list and match the slug.
    for page in range(1, 12):
        data = _get("theatres", {"page-size": 100, "page-number": page})
        theatres = data.get("_embedded", {}).get("theatres", [])
        if not theatres:
            break
        for t in theatres:
            if t.get("slug") == AMC_THEATRE_SLUG:
                return int(t["id"]), t.get("name", "")
    raise RuntimeError(f"Could not resolve theatre id for {AMC_THEATRE_SLUG!r}")


def _is_imax(showtime: dict) -> bool:
    fmt = str(showtime.get("premiumFormat", "")).lower()
    if "imax" in fmt:
        return True
    # Attributes can also carry the IMAX marker.
    for attr in showtime.get("attributes", []) or []:
        blob = f"{attr.get('code', '')} {attr.get('name', '')}".lower()
        if "imax" in blob:
            return True
    return False


def iter_showtimes(theatre_id: int, date_iso: str) -> list[dict]:
    """All showtime objects for a theatre+date (follows pagination).

    AMC returns 404 ("No showtimes found.") for a date with nothing scheduled —
    that's a valid empty result, not an error, so we return []. Other non-200s
    (auth, server errors) still raise.
    """
    out: list[dict] = []
    page = 1
    while page <= 20:  # safety cap
        r = requests.get(
            f"{API}/theatres/{theatre_id}/showtimes/{date_iso}",
            params={"page-size": 100, "page-number": page},
            headers=_headers(), timeout=25)
        if r.status_code == 404:
            break  # no showtimes for this date — empty, not an error
        if r.status_code != 200:
            raise RuntimeError(
                f"AMC API {r.status_code} on showtimes/{date_iso}: {r.text[:160]}")
        data = r.json()
        showtimes = data.get("_embedded", {}).get("showtimes", [])
        out.extend(showtimes)
        if not data.get("_links", {}).get("next") or not showtimes:
            break
        page += 1
    return out


def match_shows(showtimes: list[dict], aliases: list[str],
                imax_only: bool) -> dict:
    """Filter a date's showtimes to a movie: {time: {minutes, sold_out}} —
    matching monitor_amc's scraper output shape."""
    shows: dict[str, dict] = {}
    for s in showtimes:
        if s.get("isCanceled"):
            continue
        name = str(s.get("movieName", "")).lower()
        if not any(a in name for a in aliases):
            continue
        if imax_only and not _is_imax(s):
            continue
        local = str(s.get("showDateTimeLocal", ""))  # "2026-12-18T19:00:00"
        if "T" not in local:
            continue
        hh, mm = local.split("T")[1].split(":")[:2]
        minutes = int(hh) * 60 + int(mm)
        meridiem = "am" if int(hh) < 12 else "pm"
        t = _fmt(_to_minutes(int(hh) % 12 or 12, int(mm), meridiem))
        sold_out = bool(s.get("isSoldOut"))
        if t not in shows:
            shows[t] = {"minutes": minutes, "sold_out": sold_out}
        else:
            shows[t]["sold_out"] = shows[t]["sold_out"] and sold_out
    return shows


def find_shows(theatre_id: int, date_iso: str, aliases: list[str],
               imax_only: bool) -> dict:
    return match_shows(iter_showtimes(theatre_id, date_iso), aliases, imax_only)


def discover(date_iso: str) -> None:
    """Print theatre id + a sample of showtimes, to verify the API + key."""
    tid, name = resolve_theatre_id()
    print(f"theatre: id={tid} name={name!r}")
    sample = iter_showtimes(tid, date_iso)
    print(f"{date_iso}: {len(sample)} showtimes total")
    for s in sample[:10]:
        print(f"  {s.get('movieName')!r} {s.get('showDateTimeLocal')} "
              f"soldOut={s.get('isSoldOut')} fmt={s.get('premiumFormat')!r} "
              f"imax={_is_imax(s)}")
