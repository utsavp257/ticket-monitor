"""imax.com — weakest source: it does not sell tickets directly and isn't
reliably date-filterable, so it mostly tells us a film is *coming* to the
Lincoln Square IMAX rather than that tickets are on sale. Kept because you
asked to keep all three sources."""

from __future__ import annotations

from config import MOVIES, imax_url
from dates import target_dates
from scrape import fetch, find_earliest_show


def check_imax(debug: bool = False, movies: dict | None = None) -> list[dict]:
    movies = movies or MOVIES
    results: list[dict] = []
    # imax.com isn't date-parameterized, so fetch once and test each date.
    dates = target_dates()
    if not dates:
        return results
    url = imax_url(dates[0].isoformat())
    dump = "debug_imax.html" if debug else None
    html, text = fetch(url, debug_dump=dump)
    for d in dates:
        date_iso = d.isoformat()
        for movie, aliases in movies.items():
            show = find_earliest_show(html, text, aliases, date_iso)
            if show:
                results.append({
                    "source": "IMAX",
                    "movie": movie,
                    "date": date_iso,
                    "weekday": d.strftime("%A"),
                    "first_show": show["time"],
                    "show_count": show["shows"],
                    "url": url,
                })
    return results
