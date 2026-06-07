"""AMC Lincoln Square 13 — the real Lincoln Square IMAX, and the authoritative
ticket source.

For each watched movie we check only the first WATCH_WEEKS Tuesdays/Wednesdays
on/after its from_date (so we don't scan months of empty dates). Pages are
cached per date within a run so two movies sharing a date fetch once.
"""

from __future__ import annotations

from datetime import date

from config import MOVIES, IMAX_ONLY, amc_url
from dates import watch_dates, movie_start
from scrape import fetch, find_shows


def check_amc(debug: bool = False, movies: dict | None = None) -> list[dict]:
    movies = movies or MOVIES
    today = date.today()
    cache: dict[str, tuple] = {}
    results: list[dict] = []

    for movie, spec in movies.items():
        aliases = spec["aliases"] if isinstance(spec, dict) else spec
        for d in watch_dates(movie_start(spec, today)):
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
    return results
