"""Work out which calendar dates we actually need to check.

You want the Tuesday/Wednesday shows for each movie, but the movies are months
apart, so we watch a small window of Tue/Wed dates per movie starting at its
configured from_date rather than scanning everything in between.
"""

from __future__ import annotations

from datetime import date, timedelta

from config import TARGET_WEEKDAYS, WATCH_WEEKS, MOVIES


def next_occurrence(weekday: int, start: date) -> date:
    """First date on/after `start` that falls on `weekday` (Mon=0..Sun=6)."""
    days_ahead = (weekday - start.weekday()) % 7
    return start + timedelta(days=days_ahead)


def watch_dates(start: date, weeks: int = WATCH_WEEKS) -> list[date]:
    """First `weeks` occurrences of each TARGET_WEEKDAY on/after `start`."""
    dates: list[date] = []
    for weekday in TARGET_WEEKDAYS:
        first = next_occurrence(weekday, start)
        for week in range(weeks):
            dates.append(first + timedelta(weeks=week))
    return sorted(set(dates))


def movie_start(spec, today: date) -> date:
    """Where to start watching a movie: its from_date, but never in the past."""
    from_date = spec.get("from_date") if isinstance(spec, dict) else None
    if from_date:
        return max(today, date.fromisoformat(from_date))
    return today


if __name__ == "__main__":
    # Quick sanity check: `python src/dates.py`
    today = date.today()
    for movie, spec in MOVIES.items():
        print(movie + ":")
        for d in watch_dates(movie_start(spec, today)):
            print("  ", d.isoformat(), d.strftime("%A"))
