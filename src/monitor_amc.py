"""AMC Lincoln Square 13 — the real Lincoln Square IMAX, and the authoritative
ticket source.

For each watched movie we check only the first WATCH_WEEKS occurrences of its
watched weekdays on/after its from_date (so we don't scan months of empty
dates). Pages are cached per date within a run so two movies sharing a date
fetch once.
"""

from __future__ import annotations

from datetime import date

from config import MOVIES, IMAX_ONLY, amc_url
from dates import movie_watch_dates
from scrape import fetch, find_shows, count_listings


def check_amc(debug: bool = False, movies: dict | None = None):
    """Returns (results, health).

    health = {dates_total, dates_fetched, total_listings} lets the caller detect
    a broken scraper: every fetch failing (unreachable) or pages with zero movie
    listings (URL/layout changed).
    """
    movies = movies or MOVIES
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
        "dates_total": len(cache),
        "dates_fetched": len(fetched),
        "total_listings": sum(count_listings(text) for _html, text in fetched),
    }
    return results, health
