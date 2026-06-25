"""AMC Lincoln Square 13 — the real Lincoln Square IMAX, and the authoritative
ticket source.

For each watched movie we check only the first WATCH_WEEKS occurrences of its
watched weekdays on/after its from_date (so we don't scan months of empty
dates). Pages are cached per date within a run so two movies sharing a date
fetch once.
"""

from __future__ import annotations

from datetime import date

from collections import defaultdict

from config import MOVIES, IMAX_ONLY, AMC_THEATRE_ID, amc_url
from dates import movie_watch_dates
from scrape import fetch, find_shows, count_listings
from state import load_state, save_state
import amc_api


def check_amc(debug: bool = False, movies: dict | None = None):
    """Returns (results, health). Uses the official AMC API when AMC_VENDOR_KEY
    is set (reliable, unblocked), else falls back to HTML scraping. If the API
    key is rejected/unavailable (e.g. not activated yet), fall back to scraping
    so the monitor keeps working."""
    movies = movies or MOVIES
    if amc_api.is_configured():
        try:
            return _check_via_api(movies)
        except Exception as e:
            print(f"  ! AMC API unavailable ({str(e).splitlines()[0]}); "
                  "falling back to scrape")
    return _check_via_scrape(debug, movies)


def _check_via_api(movies: dict):
    """Official API path — lightweight JSON, no Cloudflare wall. Each date is
    fetched once and matched against every movie watching it."""
    today = date.today()
    tid = AMC_THEATRE_ID
    if not tid:
        st = load_state()
        tid = st.get("amc_theatre_id")
        if not tid:
            tid = amc_api.resolve_theatre_id()[0]
            st["amc_theatre_id"] = tid
            save_state(st)

    # date -> [(movie, aliases)] for the movies watching that date
    date_movies: dict[str, list] = defaultdict(list)
    for movie, spec in movies.items():
        aliases = spec["aliases"] if isinstance(spec, dict) else spec
        for d in movie_watch_dates(spec, today):
            date_movies[d.isoformat()].append((movie, aliases))

    results: list[dict] = []
    ok = 0
    total = 0
    for iso in sorted(date_movies):
        try:
            showtimes = amc_api.iter_showtimes(tid, iso)
        except Exception as e:
            print(f"  ! AMC API {iso} failed: {str(e).splitlines()[0]}")
            continue
        ok += 1
        total += len(showtimes)
        wd = date.fromisoformat(iso).strftime("%A")
        for movie, aliases in date_movies[iso]:
            shows = amc_api.match_shows(showtimes, aliases, imax_only=IMAX_ONLY)
            if shows:
                results.append({"movie": movie, "date": iso, "weekday": wd,
                                "url": amc_url(iso), "shows": shows})
    # source=api: 0 matching shows on far-future dates is normal, NOT "broken";
    # only all-calls-failing (ok==0) signals a problem.
    health = {"source": "api", "dates_total": len(date_movies),
              "dates_fetched": ok, "total_listings": total}
    return results, health


def _check_via_scrape(debug: bool, movies: dict):
    """Fallback: scrape the consumer site (subject to IP blocking)."""
    today = date.today()
    cache: dict[str, tuple] = {}
    results: list[dict] = []

    for movie, spec in movies.items():
        aliases = spec["aliases"] if isinstance(spec, dict) else spec
        for d in movie_watch_dates(spec, today):
            iso = d.isoformat()
            if iso not in cache:
                dump = f"debug_amc_{iso}.html" if debug else None
                try:
                    cache[iso] = fetch(amc_url(iso), debug_dump=dump)
                except Exception as e:
                    print(f"  ! {iso} fetch failed: {str(e).splitlines()[0]}")
                    cache[iso] = None
            if cache[iso] is None:
                continue
            _html, text = cache[iso]
            shows = find_shows(text, aliases, imax_only=IMAX_ONLY)
            if shows:
                results.append({
                    "movie": movie,
                    "date": iso,
                    "weekday": d.strftime("%A"),
                    "url": amc_url(iso),
                    "shows": shows,  # {time: {minutes, sold_out}}
                })

    fetched = [v for v in cache.values() if v is not None]
    health = {
        "source": "scrape",
        "dates_total": len(cache),
        "dates_fetched": len(fetched),
        "total_listings": sum(count_listings(text) for _html, text in fetched),
    }
    return results, health
