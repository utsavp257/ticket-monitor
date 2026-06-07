"""Ticket monitor entry point.

Usage:
  python src/main.py                 # normal run: check sites, send alerts
  python src/main.py --dry-run       # check sites, print matches, send nothing
  python src/main.py --debug         # also dump fetched HTML to debug_*.html
  python src/main.py --test-telegram # send one test message and exit
  python src/main.py --dates         # print which dates will be checked

Flags can be combined, e.g. `--dry-run --debug`.
"""

import sys

from monitor_amc import check_amc
from monitor_fandango import check_fandango
from monitor_imax import check_imax
from dates import target_dates
import telegram
from state import already_seen, mark_seen

SOURCES = [
    ("AMC", check_amc),
    ("Fandango", check_fandango),
    ("IMAX", check_imax),
]


def alert_key(item: dict) -> str:
    # Include date + first show time so a *new earlier* show re-alerts, but a
    # stable listing doesn't notify on every run.
    return f"{item['source']}|{item['movie']}|{item['date']}|{item['first_show']}"


def format_alert(item: dict) -> str:
    return (
        "🎬 Ticket Alert — first show is up!\n\n"
        f"Movie: {item['movie']}\n"
        f"Day:   {item['weekday']} {item['date']}\n"
        f"First show: {item['first_show']}  "
        f"({item['show_count']} showtime(s) listed)\n"
        f"Source: {item['source']}\n"
        f"Book: {item['url']}"
    )


def process(results: list[dict], dry_run: bool) -> int:
    sent = 0
    for item in results:
        key = alert_key(item)
        if already_seen(key):
            print(f"  · already alerted: {key}")
            continue
        message = format_alert(item)
        if dry_run:
            print("  --- would send ---")
            print(message)
            print("  ------------------")
        else:
            if telegram.send_message(message):
                mark_seen(key)
                sent += 1
                print(f"  ✓ alerted + saved: {key}")
            else:
                # Don't mark seen if the notification failed — retry next run.
                print(f"  ! send failed, will retry: {key}")
    return sent


def run(dry_run: bool, debug: bool) -> None:
    if dry_run:
        print("DRY RUN — no messages will be sent.\n")
    total = 0
    for name, check in SOURCES:
        print(f"Checking {name}...")
        try:
            results = check(debug=debug)
        except Exception as e:
            # Isolate failures: one source breaking must not stop the others.
            print(f"  ! {name} check failed: {e}")
            continue
        if not results:
            print(f"  · no matching showtimes found on {name}")
        total += process(results, dry_run)
    print(f"\nDone. {total} new alert(s) sent.")


def main() -> None:
    args = set(sys.argv[1:])

    if "--dates" in args:
        for d in target_dates():
            print(d.isoformat(), d.strftime("%A"))
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
