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
import time
from datetime import date

from monitor_amc import check_amc
from monitor_instagram import check_instagram
from dates import movie_watch_dates
from config import (MOVIES, IG_CHECK_EVERY_HOURS, FAILURE_ALERT_COOLDOWN_HOURS,
                    AMC_FAIL_STREAK_FOR_ALERT)
import telegram
import pushover
import healthcheck
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


def _escalate_policy(movie: str) -> str:
    """Per-movie Pushover policy from MOVIES (default 'new')."""
    spec = MOVIES.get(movie)
    return spec.get("escalate", "new") if isinstance(spec, dict) else "new"


def _escalation_times(item, cur, new_times, freed, armed_map) -> list[str]:
    """Which showtimes (if any) should fire a Pushover siren, per policy.

    - 'never': never escalate (e.g. The Odyssey).
    - 'new':   escalate on a new *available* showtime (buy-now moment).
    - 'new_then_seats': escalate on new available showtimes; a new show of any
      kind 'arms' the movie, after which seat-frees (sold-out -> available) also
      escalate. Before it's armed, nothing escalates.
    """
    movie = item["movie"]
    policy = _escalate_policy(movie)
    if policy == "never":
        return []
    new_available = [t for t in new_times if not cur[t]]
    if policy == "new":
        times = new_available
    elif policy == "new_then_seats":
        if new_times:                      # a new show "opened up" -> arm it
            armed_map[movie] = True
        times = list(new_available)
        if armed_map.get(movie) and freed:  # only siren seat-frees once armed
            times += freed
    else:
        times = new_available
    return _ordered(item["shows"], list(dict.fromkeys(times)))


def diff_and_alert(results: list[dict], dry_run: bool) -> int:
    """Compare current showtimes against saved state; alert on changes."""
    state = load_state()
    saved = state["shows"]  # {"movie|date": {time: sold_out}}
    armed_map = state.setdefault("escalation_armed", {})  # {movie: True}
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

        escalate = _escalation_times(item, cur, new_times, freed, armed_map)

        message = compose_alert(item, new_times, freed)
        if dry_run:
            print("  --- would send ---")
            print(message)
            if escalate:
                print(f"  --- would ESCALATE (Pushover): {', '.join(escalate)}")
            print("  ------------------")
            continue
        ok = telegram.send_message(message)
        if escalate:
            pushover.send_emergency(
                message=(f"{item['movie']} IMAX — {item['weekday']} "
                         f"{item['date']}\nGrab now: {', '.join(escalate)}"),
                title="🎟️ IMAX seats available",
                url=item["url"],
                url_title="Book on AMC",
            )
        if ok:
            saved[key] = cur  # only commit state once the alert is delivered
            sent += 1
            print(f"  ✓ alerted: {key} (+{len(new_times)} new, "
                  f"{len(freed)} freed, {len(escalate)} escalated)")
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
        results, _health = check_amc(movies=movies)
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


def ig_diff_and_alert(posts: list[dict], dry_run: bool) -> int:
    """Alert on new Instagram posts. On the first run we just record what's
    already there (baseline) so we don't blast a backlog of old posts."""
    if not posts:
        # Fetch failed (e.g. IP-blocked) — do nothing rather than baseline an
        # empty set, which would later flag every real post as "new".
        print("  · no posts retrieved; skipping")
        return 0

    state = load_state()
    if "ig_seen" not in state:
        state["ig_seen"] = sorted({p["shortcode"] for p in posts})
        if not dry_run:
            save_state(state)
        print(f"  · baseline set ({len(posts)} existing posts, no alert)")
        return 0

    seen = set(state["ig_seen"])
    new = sorted((p for p in posts if p["shortcode"] not in seen),
                 key=lambda p: p["timestamp"])
    sent = 0
    for p in new:
        caption = p["caption"].strip()
        if len(caption) > 300:
            caption = caption[:300] + "…"
        message = (
            f"📸 New @{p['username']} Instagram post\n\n"
            f"{caption}\n\n"
            f"https://www.instagram.com/p/{p['shortcode']}/"
        )
        if dry_run:
            print("  --- would send ---")
            print(message)
            print("  ------------------")
        elif telegram.send_message(message):
            sent += 1
            print(f"  ✓ alerted: IG {p['shortcode']}")
        else:
            print(f"  ! send failed, will retry next run: IG {p['shortcode']}")
    if posts and not dry_run:
        # Remember the current window (bounded — IG returns ~12 recent posts).
        state["ig_seen"] = sorted({p["shortcode"] for p in posts})
        save_state(state)
    return sent


def alert_failure(reason: str, dry_run: bool) -> None:
    """Telegram alert when the monitor looks broken (e.g. AMC URL/layout change
    or outage), throttled so a persistent problem doesn't spam every run."""
    if dry_run:
        print(f"  ! would send FAILURE alert: {reason}")
        return
    state = load_state()
    last = state.get("last_failure_alert", 0)
    if time.time() - last < FAILURE_ALERT_COOLDOWN_HOURS * 3600:
        print(f"  ! problem (alert throttled): {reason}")
        return
    message = (
        "⚠️ Ticket monitor problem\n\n"
        f"{reason}\n\n"
        "It may be missing showtimes until this is looked at."
    )
    if telegram.send_message(message):
        state["last_failure_alert"] = time.time()
        save_state(state)
        print(f"  ! FAILURE alert sent: {reason}")
    else:
        print(f"  ! could not send failure alert: {reason}")


def run(dry_run: bool, debug: bool) -> None:
    if dry_run:
        print("DRY RUN — no messages will be sent.\n")
    total = 0

    print("Checking AMC Lincoln Square...")
    broken = None  # set to a reason string if this run looks broken
    try:
        results, health = check_amc(debug=debug)
        if not results:
            print("  · no showtimes yet on any watched date")
        total += diff_and_alert(results, dry_run)
        # Distinguish "nothing on sale" from "scraper is broken".
        if health["dates_total"] and health["dates_fetched"] == 0:
            broken = ("AMC was unreachable — every page fetch failed (IP block "
                      "or outage).")
        elif health["dates_fetched"] and health["total_listings"] == 0:
            broken = ("AMC pages returned no movie listings at all — the "
                      "showtimes URL or page layout has likely changed.")
    except Exception as e:
        print(f"  ! AMC check failed: {e}")
        broken = f"AMC check crashed: {e}"

    # Debounce: only alert after AMC_FAIL_STREAK_FOR_ALERT consecutive failures,
    # so a single transient timeout (self-recovers next run) doesn't cry wolf.
    if not dry_run:
        st = load_state()
        streak = st.get("amc_fail_streak", 0) + 1 if broken else 0
        st["amc_fail_streak"] = streak
        save_state(st)
        if broken and streak >= AMC_FAIL_STREAK_FOR_ALERT:
            alert_failure(f"{broken} (failed {streak} runs in a row)", dry_run)
        elif broken:
            print(f"  · AMC problem (streak {streak}/"
                  f"{AMC_FAIL_STREAK_FOR_ALERT}; alerting only if it persists)")
    elif broken:
        print(f"  ! would track AMC problem: {broken}")

    print("Checking Instagram...")
    last = load_state().get("ig_last_check", 0)
    if not dry_run and time.time() - last < IG_CHECK_EVERY_HOURS * 3600:
        hrs = round((time.time() - last) / 3600, 1)
        print(f"  · skipped (last checked {hrs}h ago; "
              f"every {IG_CHECK_EVERY_HOURS}h to spare Apify credits)")
    else:
        try:
            total += ig_diff_and_alert(check_instagram(), dry_run)
        except Exception as e:
            print(f"  ! Instagram check failed: {e}")
        if not dry_run:
            st = load_state()
            st["ig_last_check"] = time.time()
            save_state(st)

    print(f"\nDone. {total} alert(s) sent.")


def main() -> None:
    argv = sys.argv[1:]
    args = set(argv)

    if "--dates" in args:
        today = date.today()
        for movie, spec in MOVIES.items():
            print(f"{movie} (watching):")
            for d in movie_watch_dates(spec, today):
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

    if "--test-pushover" in args:
        ok = pushover.send_emergency(
            message="If your phone is sirening, escalation works. Tap to "
                    "acknowledge to stop it.",
            title="🎟️ Ticket monitor — TEST",
            expire=300,  # auto-stops after 5 min even if not acknowledged
        )
        print("Sent (emergency, ~5 min). It should siren until you acknowledge."
              if ok else "Failed — check PUSHOVER_TOKEN / PUSHOVER_USER.")
        return

    if "--test-escalation" in args:
        # Fire the exact siren a real "Dune new showtime on sale" would produce,
        # so you can see/hear it. Touches no state (doesn't arm Dune).
        ok = pushover.send_emergency(
            message="Dune: Part Three IMAX — Tuesday 2026-12-22\n"
                    "Grab now: 10:30am, 7:00pm",
            title="🎟️ IMAX seats available",
            url=("https://www.amctheatres.com/movie-theatres/new-york-city/"
                 "amc-lincoln-square-13/showtimes/all/2026-12-22/"
                 "amc-lincoln-square-13/all"),
            url_title="Book on AMC",
            expire=300,
        )
        print("Sent the Dune new-show escalation (emergency, ~5 min)."
              if ok else "Failed — check PUSHOVER_TOKEN / PUSHOVER_USER.")
        return

    if "--health-check" in args:
        # exit 0 even on failure — the alert is the Telegram, not a red run
        healthcheck.run_and_notify(announce="--announce" in args)
        return

    run(dry_run="--dry-run" in args, debug="--debug" in args)


if __name__ == "__main__":
    main()
