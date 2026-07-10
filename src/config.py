"""Central configuration for the ticket monitor.

Everything you might need to tweak lives here so you don't have to dig
through the scraping code.
"""

# --- What movies are we watching for? ---------------------------------------
#
# We match on title *aliases* because theaters rarely use the casual name.
# "Dune 3" will almost certainly be listed as "Dune: Part Three" or
# "Dune Messiah", and "Odyssey" as "The Odyssey". Add/remove aliases here as
# the official titles get confirmed. Matching is case-insensitive.
#
# `aliases`   : case-insensitive title fragments to match against AMC listings.
# `from_date` : don't bother checking before this date (keeps us from scanning
#               months of empty Tuesdays/Wednesdays every run). Set it to around
#               each movie's release; the monitor watches the first WATCH_WEEKS
#               Tuesdays/Wednesdays on/after it. Verified against live AMC:
#               The Odyssey first plays Tue Jul 21 2026; Dune: Part Three opens
#               Fri Dec 18 2026 (its first Tue/Wed = Dec 22/23, not yet on sale).
#
# `weekdays`  : optional per-movie override of which days to watch
#               (Mon=0 .. Sun=6). Omit to use the global TARGET_WEEKDAYS.
# `escalate`  : Pushover-siren policy (default "new"):
#                 "never"          - never siren (Telegram only)
#                 "new"            - siren on a new available showtime
#                 "new_then_seats" - siren on new available showtimes; once a new
#                                    show has appeared, also siren on seat-frees
MOVIES = {
    "Dune: Part Three": {
        "aliases": ["dune: part three", "dune part three"],
        "from_date": "2026-12-15",
        "weekdays": [0, 1, 2, 3, 4, 5, 6],  # every day, not just Tue/Wed
        "escalate": "new_then_seats",       # siren on new shows, then seat-frees
    },
    "The Odyssey": {
        "aliases": ["the odyssey"],
        "from_date": "2026-07-14",
        # no "weekdays" -> uses TARGET_WEEKDAYS (Tue/Wed)
        "escalate": "never",                # Telegram only, no siren ever
    },
}

# Only alert on IMAX-format showtimes (IMAX 70mm / IMAX with Laser). Set to
# False to include all formats (Dolby, Laser, standard, etc.).
IMAX_ONLY = True

# Instagram handles to watch for new posts (they often post when tickets go
# live). Add more, e.g. the Odyssey film's account, as needed.
INSTAGRAM_ACCOUNTS = ["dunemovie"]

# Instagram blocks datacenter IPs, so on CI we go through Apify (residential
# proxies) when an APIFY_TOKEN is set; otherwise we try the free direct endpoint
# (works locally, usually 429s on CI). Free Apify credits are limited, so we
# only actually hit Instagram every IG_CHECK_EVERY_HOURS hours, and we rotate
# across up to three keys (APIFY_TOKEN, APIFY_TOKEN_2, APIFY_TOKEN_3) to spread
# the load — see monitor_instagram.check_instagram.
#
# Cost budget: the actor (apify/instagram-scraper) is pay-per-result at
# $2.70 / 1,000 results; one scrape = 1 account x 12 results. So monthly spend
# ~= (730 / IG_CHECK_EVERY_HOURS) * 12 * $0.0027. At 1.9h that's ~384 scrapes/mo
# ~= $12.4, which split across the 3 rotating keys is ~$4.15 each — under every
# key's $5/mo free credit (and ~$12.4 of the combined $15 free). Raise this
# number to spend less; lower it (more frequent checks) to spend more.
APIFY_ACTOR = "apify~instagram-scraper"
IG_CHECK_EVERY_HOURS = 1.9

# --- Dune Insider email watch ------------------------------------------------
# Warner Bros sends the "Dune Insider" newsletter — and, we expect, ticket
# on-sale announcements — from EMAIL_SENDER. We read the mailbox over IMAP
# (free, stdlib) using a Gmail App Password in GMAIL_USER / GMAIL_APP_PASSWORD;
# without those set the email check is skipped. See monitor_email.py.
EMAIL_IMAP_HOST = "imap.gmail.com"
# "[Gmail]/All Mail" catches messages even if a Gmail filter skips the inbox.
# Change to "INBOX" if you'd rather only watch the inbox.
EMAIL_FOLDER = '"[Gmail]/All Mail"'
# Match mail FROM this sender OR whose text contains EMAIL_TEXT_MATCH.
EMAIL_SENDER = "warnerbros@updates.warnerbros.com"
EMAIL_TEXT_MATCH = "Dune Insider"
# Only search the last N days (dedup by Message-ID handles re-alerts; this just
# bounds how much we scan/fetch each run) and cap how many we pull per run.
EMAIL_SINCE_DAYS = 7
EMAIL_MAX_FETCH = 30
# Task 2 (keyword parse): if any of these appear in a matching email, also fire
# the Pushover siren — a ticket on-sale signal. TUNE THIS once we've seen a real
# WB on-sale email (run `python src/main.py --test-email` to dump the format).
# Bias toward recall: Telegram forwards every email regardless, so a missed
# phrase still reaches you, while a false hit is only one extra siren.
EMAIL_ONSALE_PHRASES = [
    "on sale now", "now on sale", "tickets are now available",
    "tickets are available", "tickets available now", "tickets are live",
    "get tickets", "buy tickets", "book now", "book your tickets",
    "presale", "pre-sale", "tickets on sale", "on sale",
]

# Send a Telegram alert if the monitor looks broken — AMC unreachable, or its
# pages parse to zero movie listings (a sign the URL/layout changed). Throttled
# to this many hours so a persistent outage doesn't spam every run.
FAILURE_ALERT_COOLDOWN_HOURS = 3

# Only alert "AMC broken" after this many CONSECUTIVE failed runs, so a single
# transient datacenter-IP timeout (which self-recovers next run) doesn't cry
# wolf. 2 ≈ a sustained ~10-min outage before you're pinged.
AMC_FAIL_STREAK_FOR_ALERT = 2

# AMC rate-limits/blocks datacenter IPs under load. Even if the cron fires more
# often, only actually hit AMC this often — keeps our footprint low so the
# shared GitHub-runner IP doesn't get blocked. Lower it when a release is
# imminent and you need tighter timing (accepting more block risk).
# Set to 30 while the movies are months out — minimal footprint to let AMC's
# block cool off. Drop toward 5 as Jul (Odyssey) / Dec (Dune) approach.
AMC_CHECK_EVERY_MINUTES = 30

# AMC Lincoln Square 13 slug — used to resolve its numeric id via the official
# API (api.amctheatres.com). The API is used when AMC_VENDOR_KEY is set; it's
# lightweight JSON, not behind Cloudflare, so it doesn't block our IP.
AMC_THEATRE_SLUG = "amc-lincoln-square-13"
# Filled in after discovery (the numeric theatre id) to skip per-run lookup.
AMC_THEATRE_ID = None

# Pushover "emergency" escalation for the buy-now moment only (a NEW *available*
# showtime going on sale — not seat-frees, which could be one bad seat). Pushover
# re-sirens every PUSHOVER_RETRY sec until you acknowledge in the app, for up to
# PUSHOVER_EXPIRE sec. Needs PUSHOVER_TOKEN / PUSHOVER_USER in the environment.
PUSHOVER_RETRY = 30       # seconds between re-alerts (Pushover minimum is 30)
PUSHOVER_EXPIRE = 1800    # give up re-alerting after 30 min if not acknowledged
PUSHOVER_SOUND = "siren"  # built-in Pushover sound; "siren"/"persistent"/"alien"

# --- Which days do we care about? --------------------------------------------
# Monday=0 ... Sunday=6. You asked for Tuesday and Wednesday.
TARGET_WEEKDAYS = [1, 2]  # Tuesday, Wednesday

# How many of each target weekday to watch per movie, starting at its from_date.
# 2 = the first two Tuesdays and first two Wednesdays (a little fallback buffer).
WATCH_WEEKS = 2

# --- Where do we look? -------------------------------------------------------
# Each source builds a *date-specific* showtimes URL via a function that takes
# an ISO date string (YYYY-MM-DD). These are the parts most likely to need a
# tweak if a site changes its URL scheme — see README for how to verify them.

# AMC Lincoln Square 13 is the real "Lincoln Square IMAX" and the authoritative
# ticket source. (Fandango/IMAX were dropped — redundant, and neither exposed
# usable showtimes to the scraper.)
def amc_url(date_iso: str) -> str:
    return (
        "https://www.amctheatres.com/movie-theatres/new-york-city/"
        "amc-lincoln-square-13/showtimes/all/"
        f"{date_iso}/amc-lincoln-square-13/all"
    )


# --- Scraper behavior --------------------------------------------------------
PAGE_TIMEOUT_MS = 60_000
# Extra settle time after load for JS-rendered showtimes (milliseconds).
SETTLE_MS = 4_000
# Retries per source on transient failures / bot challenges (with backoff).
MAX_RETRIES = 3
