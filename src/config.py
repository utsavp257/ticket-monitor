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
MOVIES = {
    "Dune 3": [
        "dune 3",
        "dune: part three",
        "dune part three",
        "dune messiah",
        "dune: messiah",
    ],
    "Odyssey": [
        "the odyssey",
        "odyssey",
    ],
}

# --- Which days do we care about? --------------------------------------------
# Monday=0 ... Sunday=6. You asked for Tuesday and Wednesday.
TARGET_WEEKDAYS = [1, 2]  # Tuesday, Wednesday

# How many upcoming occurrences of each target weekday to check.
# 1 = just the very next Tuesday and next Wednesday.
WEEKS_AHEAD = 1

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
