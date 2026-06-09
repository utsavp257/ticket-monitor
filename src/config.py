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
MOVIES = {
    "Dune: Part Three": {
        "aliases": ["dune: part three", "dune part three"],
        "from_date": "2026-12-15",
        "weekdays": [0, 1, 2, 3, 4, 5, 6],  # every day, not just Tue/Wed
    },
    "The Odyssey": {
        "aliases": ["the odyssey"],
        "from_date": "2026-07-14",
        # no "weekdays" -> uses TARGET_WEEKDAYS (Tue/Wed)
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
# only actually hit Instagram every IG_CHECK_EVERY_HOURS hours.
APIFY_ACTOR = "apify~instagram-scraper"
IG_CHECK_EVERY_HOURS = 6

# Send a Telegram alert if the monitor looks broken — AMC unreachable, or its
# pages parse to zero movie listings (a sign the URL/layout changed). Throttled
# to this many hours so a persistent outage doesn't spam every run.
FAILURE_ALERT_COOLDOWN_HOURS = 3

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
