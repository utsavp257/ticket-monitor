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
from urllib.parse import quote

from monitor_amc import check_amc
from monitor_instagram import check_instagram
import monitor_email
from dates import movie_watch_dates
from config import (MOVIES, IG_CHECK_EVERY_HOURS, FAILURE_ALERT_COOLDOWN_HOURS,
                    AMC_FAIL_STREAK_FOR_ALERT, AMC_CHECK_EVERY_MINUTES)
import telegram
import pushover
import healthcheck
import amc_api
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


def email_diff_and_alert(emails: list[dict], dry_run: bool) -> int:
    """Dune Insider email watch.

    Task 1 (definitive): forward every new matching email to Telegram in full.
    Task 2 (keyword): if it contains an on-sale phrase, also fire a Pushover
    siren. The first run baselines existing matches (records them, no alert) so
    we don't blast the backlog — mirrors the Instagram behaviour."""
    if not emails:
        # Nothing matched (or the fetch was empty) — don't baseline an empty set
        # over an existing one; just leave state alone.
        return 0

    state = load_state()
    if "email_seen" not in state:
        state["email_seen"] = sorted(e["msg_id"] for e in emails)
        if not dry_run:
            save_state(state)
        print(f"  · baseline set ({len(emails)} existing email(s), no alert)")
        return 0

    seen = set(state["email_seen"])
    # oldest first so, if several arrive at once, alerts land in reading order
    new = [e for e in reversed(emails) if e["msg_id"] not in seen]
    sent = 0
    for e in new:
        gmail_link = ("https://mail.google.com/mail/u/0/#search/rfc822msgid:"
                      + quote(e["msg_id"].strip("<>")))
        body = e["body"].strip()
        if len(body) > 1200:
            body = body[:1200] + "…"
        lines = [
            "📧 Dune Insider email",
            f"From: {e['from']}",
            f"Subject: {e['subject']}",
            "",
            body,
        ]
        if e["onsale"]:
            lines += ["", f"🎟️ Possible ON-SALE signal: {', '.join(e['onsale'])}"]
        lines += ["", f"Open in Gmail: {gmail_link}"]
        message = "\n".join(lines)

        if dry_run:
            print("  --- would send ---")
            print(message)
            if e["onsale"]:
                print(f"  --- would ESCALATE (Pushover): {', '.join(e['onsale'])}")
            print("  ------------------")
            continue

        ok = telegram.send_message(message)
        if ok and e["onsale"]:
            pushover.send_emergency(
                message=(f"Dune Insider: {e['subject']}\n"
                         f"Signal: {', '.join(e['onsale'])}"),
                title="🎟️ Dune tickets — on-sale email",
                url=gmail_link,
                url_title="Open email",
            )
        if ok:
            seen.add(e["msg_id"])  # only mark seen once delivered → retry on fail
            sent += 1
            print(f"  ✓ alerted: email {e['subject'][:60]!r} "
                  f"({len(e['onsale'])} on-sale hit(s))")
        else:
            print(f"  ! send failed, will retry next run: email {e['msg_id']}")

    if not dry_run:
        # Keep only delivered ids still inside the current search window, so the
        # set stays bounded and an undelivered (failed) id is retried next run.
        current = {e["msg_id"] for e in emails}
        state["email_seen"] = sorted(seen & current)
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


def check_api_activation(dry_run: bool) -> None:
    """One-time: when the AMC API key first authorizes, send a Telegram. Also
    flips amc_api_confirmed in state, which lifts the scrape throttle (→ 5-min)."""
    if not amc_api.is_configured():
        return
    state = load_state()
    if state.get("amc_api_confirmed"):
        return
    if not amc_api.ping():
        print("  · AMC API key not authorized yet")
        return
    print("  ✓ AMC API key is now authorized!")
    if dry_run:
        return
    if telegram.send_message(
            "✅ AMC API key is LIVE\n\nThe monitor just switched to AMC's "
            "official API — no more datacenter-IP blocking — and is now checking "
            "every 5 minutes."):
        state["amc_api_confirmed"] = True
        save_state(state)


def run(dry_run: bool, debug: bool) -> None:
    if dry_run:
        print("DRY RUN — no messages will be sent.\n")
    total = 0

    # Detect first-time API activation (and notify); lifts the throttle below.
    check_api_activation(dry_run)
    api_live = amc_api.is_configured() and load_state().get("amc_api_confirmed")

    print("Checking AMC Lincoln Square...")
    amc_last = load_state().get("amc_last_check", 0)
    # The throttle exists to avoid AMC blocking the scraper IP. The official API
    # doesn't block, so when it's live we check every run (5-min cron).
    if (not dry_run and not api_live
            and time.time() - amc_last < AMC_CHECK_EVERY_MINUTES * 60):
        mins = round((time.time() - amc_last) / 60)
        print(f"  · skipped (checked {mins}m ago; every "
              f"{AMC_CHECK_EVERY_MINUTES}m to avoid AMC rate-limiting our IP)")
    else:
        broken = None  # set to a reason string if this run looks broken
        try:
            results, health = check_amc(debug=debug)
            if not results:
                print("  · no showtimes yet on any watched date")
            total += diff_and_alert(results, dry_run)
            # Distinguish "nothing on sale" from "scraper is broken".
            if health["dates_total"] and health["dates_fetched"] == 0:
                broken = ("AMC was unreachable — every fetch failed "
                          "(IP block, bad API key, or outage).")
            elif (health.get("source") == "scrape"
                  and health["dates_fetched"] and health["total_listings"] == 0):
                # Only meaningful when scraping — the API legitimately returns
                # no matches for far-future dates.
                broken = ("AMC returned pages with zero movie listings — likely "
                          "our datacenter IP is being rate-limited/blocked (it "
                          "works from a normal connection), or the layout changed.")
        except Exception as e:
            print(f"  ! AMC check failed: {e}")
            broken = f"AMC check crashed: {e}"

        # Debounce: alert only after AMC_FAIL_STREAK_FOR_ALERT consecutive
        # failures, so a single transient blip doesn't cry wolf.
        if not dry_run:
            st = load_state()
            st["amc_last_check"] = time.time()
            st["amc_fail_streak"] = st.get("amc_fail_streak", 0) + 1 if broken else 0
            streak = st["amc_fail_streak"]
            save_state(st)
            if broken and streak >= AMC_FAIL_STREAK_FOR_ALERT:
                alert_failure(f"{broken} (failed {streak} checks in a row)",
                              dry_run)
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

    print("Checking Dune Insider email...")
    if not monitor_email.is_configured():
        print("  · skipped (GMAIL_USER / GMAIL_APP_PASSWORD not set)")
    else:
        # Reading Gmail over IMAP is free and unmetered, so — unlike Instagram —
        # we check every run for near real-time coverage of a ticket drop.
        try:
            total += email_diff_and_alert(monitor_email.check_email(), dry_run)
        except Exception as e:
            print(f"  ! Email check failed: {e}")

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

    if "--test-email" in args:
        # Inspect what the Gmail watch currently matches (no alerts sent). Use
        # this to see a real WB email's format and tune EMAIL_ONSALE_PHRASES.
        if not monitor_email.is_configured():
            print("GMAIL_USER / GMAIL_APP_PASSWORD not set.")
            return
        try:
            emails = monitor_email.check_email()
        except Exception as e:
            print(f"Email check failed: {e}")
            return
        print(f"{len(emails)} matching email(s) in the last window:\n")
        for e in emails:
            print(f"--- {e['date']} | from {e['from']}")
            print(f"Subject: {e['subject']}")
            print(f"On-sale keyword hits: {e['onsale'] or '(none)'}")
            preview = e["body"][:800]
            print(preview + ("…" if len(e["body"]) > 800 else ""))
            print()
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

    if "--discover-amc" in argv:
        import amc_api
        from datetime import date, timedelta
        if not amc_api.is_configured():
            print("AMC_VENDOR_KEY not set.")
            return
        amc_api.discover((date.today() + timedelta(days=2)).isoformat())
        return

    if "--health-check" in args:
        # exit 0 even on failure — the alert is the Telegram, not a red run
        healthcheck.run_and_notify(announce="--announce" in args)
        return

    run(dry_run="--dry-run" in args, debug="--debug" in args)


if __name__ == "__main__":
    main()
