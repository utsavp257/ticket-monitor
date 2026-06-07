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
MOVIES = {
    "Dune: Part Three": {
        "aliases": ["dune: part three", "dune part three"],
        "from_date": "2026-12-15",
    },
    "The Odyssey": {
        "aliases": ["the odyssey"],
        "from_date": "2026-07-14",
    },
}

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
