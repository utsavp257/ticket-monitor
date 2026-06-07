"""Work out which calendar dates we actually need to check.

You asked for "the first show on Tuesday and Wednesday", so we compute the
upcoming Tuesday and Wednesday (and optionally further weeks out).
"""

from __future__ import annotations

from datetime import date, timedelta

from config import TARGET_WEEKDAYS, WEEKS_AHEAD


def next_occurrence(weekday: int, start: date) -> date:
    """First date on/after `start` that falls on `weekday` (Mon=0..Sun=6)."""
    days_ahead = (weekday - start.weekday()) % 7
    return start + timedelta(days=days_ahead)


def target_dates(today: date | None = None) -> list[date]:
    """Upcoming dates matching TARGET_WEEKDAYS, sorted, for WEEKS_AHEAD weeks."""
    today = today or date.today()
    dates: list[date] = []
    for weekday in TARGET_WEEKDAYS:
        first = next_occurrence(weekday, today)
        for week in range(WEEKS_AHEAD):
            dates.append(first + timedelta(weeks=week))
    return sorted(set(dates))


if __name__ == "__main__":
    # Quick sanity check: `python src/dates.py`
    for d in target_dates():
        print(d.isoformat(), d.strftime("%A"))
