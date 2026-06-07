"""AMC Lincoln Square 13 — the real Lincoln Square IMAX, and the source that
actually sells the tickets you want."""

from __future__ import annotations

from config import MOVIES, amc_url
from dates import target_dates
from scrape import fetch, find_earliest_show


def check_amc(debug: bool = False, movies: dict | None = None) -> list[dict]:
    movies = movies or MOVIES
    results: list[dict] = []
    for d in target_dates():
        date_iso = d.isoformat()
        url = amc_url(date_iso)
        dump = f"debug_amc_{date_iso}.html" if debug else None
        html, text = fetch(url, debug_dump=dump)
        for movie, aliases in movies.items():
            show = find_earliest_show(html, text, aliases, date_iso)
            if show:
                results.append({
                    "source": "AMC",
                    "movie": movie,
                    "date": date_iso,
                    "weekday": d.strftime("%A"),
                    "first_show": show["time"],
                    "show_count": show["shows"],
                    "url": url,
                })
    return results
