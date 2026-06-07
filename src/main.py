"""Ticket monitor entry point.

Watches AMC Lincoln Square 13 for the configured movies' Tuesday/Wednesday
shows and alerts on CHANGES: a new showtime appears, or a sold-out show frees
up. It does not re-alert on shows you've already been told about.

Usage:
  python src/main.py                 # normal run: check, alert on changes
  python src/main.py --dry-run       # check + print changes, send nothing
  python src/main.py --debug         # also dump fetched HTML to debug_*.html
  python src/main.py --test-telegram # send one test message and exit
  python src/main.py --dates         # print which dates each movie watches
  python src/main.py --probe "Masters of the Universe"
                                     # end-to-end test using a movie on sale
                                     # NOW: parse it + send a real alert so you
                                     # can confirm the pipeline (--dry-run to
                                     # print instead of send).

Flags can be combined, e.g. `--dry-run --debug`.
"""

import sys
from datetime import date

from monitor_amc import check_amc
from dates import watch_dates, movie_start
from config import MOVIES
import telegram
from state import load_state, save_state


def _ordered(shows: dict, times: list[str]) -> list[str]:
    return sorted(times, key=lambda t: shows[t]["minutes"])


def compose_alert(item: dict, new_times: list[str], freed: list[str]) -> str:
    shows = item["shows"]
    lines = [f"🎬 {item['movie']} — {item['weekday']} {item['date']}"]
    if new_times:
        parts = [
            t + (" (sold out)" if shows[t]["sold_out"] else "")
            for t in _ordered(shows, new_times)
        ]
        lines.append("🆕 New showtime(s): " + ", ".join(parts))
    if freed:
        lines.append("🎟️ Seats opened up: " + ", ".join(_ordered(shows, freed)))
    available = [t for t in shows if not shows[t]["sold_out"]]
    if available:
        lines.append(f"Earliest available: {_ordered(shows, available)[0]}")
    else:
        lines.append("⚠️ All listed shows currently sold out.")
    lines.append(f"Book: {item['url']}")
    return "\n".join(lines)


def diff_and_alert(results: list[dict], dry_run: bool) -> int:
    """Compare current showtimes against saved state; alert on changes."""
    state = load_state()
    saved = state["shows"]  # {"movie|date": {time: sold_out}}
    sent = 0
    for item in results:
        key = f"{item['movie']}|{item['date']}"
        prev = saved.get(key, {})
        cur = {t: v["sold_out"] for t, v in item["shows"].items()}

        new_times = [t for t in cur if t not in prev]
        freed = [t for t in cur if t in prev and prev[t] and not cur[t]]

        if not new_times and not freed:
            saved[key] = cur  # keep status fresh (e.g. a show that sold out)
            continue

        message = compose_alert(item, new_times, freed)
        if dry_run:
            print("  --- would send ---")
            print(message)
            print("  ------------------")
            continue
        if telegram.send_message(message):
            saved[key] = cur  # only commit state once the alert is delivered
            sent += 1
            print(f"  ✓ alerted: {key} (+{len(new_times)} new, "
                  f"{len(freed)} freed)")
        else:
            print(f"  ! send failed, will retry next run: {key}")
    if not dry_run:
        save_state(state)
    return sent


def probe(title: str, dry_run: bool) -> None:
    """End-to-end test using a movie on sale now. Bypasses saved state, so it
    treats every current showtime as new and (really) sends an alert."""
    movies = {f"{title} (PROBE)": [title.lower()]}
    print(f"PROBE: testing pipeline with currently-showing title {title!r}\n")
    try:
        results = check_amc(movies=movies)
    except Exception as e:
        print(f"  ! check failed: {e}")
        return
    if not results:
        print("Not found on the watched dates — pick a title currently showing "
              "at AMC Lincoln Square and try again.")
        return
    for item in results:
        message = compose_alert(item, list(item["shows"].keys()), [])
        if dry_run:
            print("  --- would send ---")
            print(message)
            print("  ------------------")
        elif telegram.send_message(message):
            print(f"  ✓ sent: {item['date']}")
        else:
            print("  ! send failed (check Telegram creds)")


def run(dry_run: bool, debug: bool) -> None:
    if dry_run:
        print("DRY RUN — no messages will be sent.\n")
    print("Checking AMC Lincoln Square...")
    try:
        results = check_amc(debug=debug)
    except Exception as e:
        print(f"  ! AMC check failed: {e}")
        return
    if not results:
        print("  · no showtimes yet on any watched date")
    total = diff_and_alert(results, dry_run)
    print(f"\nDone. {total} alert(s) sent.")


def main() -> None:
    argv = sys.argv[1:]
    args = set(argv)

    if "--dates" in args:
        today = date.today()
        for movie, spec in MOVIES.items():
            print(f"{movie} (watching):")
            for d in watch_dates(movie_start(spec, today)):
                print("  ", d.isoformat(), d.strftime("%A"))
        return

    if "--probe" in argv:
        i = argv.index("--probe")
        title = argv[i + 1] if i + 1 < len(argv) else ""
        if not title:
            print('Usage: python src/main.py --probe "Movie Title"')
            return
        probe(title, dry_run="--dry-run" in args)
        return

    if "--test-telegram" in args:
        if telegram.send_message("✅ Ticket monitor test message — it works!"):
            print("Sent. Check your Telegram.")
        else:
            print("Failed — see error above. Check TELEGRAM_TOKEN / "
                  "TELEGRAM_CHAT_ID.")
        return

    run(dry_run="--dry-run" in args, debug="--debug" in args)


if __name__ == "__main__":
    main()
