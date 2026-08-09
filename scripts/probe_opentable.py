"""Feasibility probe: can a GitHub Actions runner read OpenTable availability?

Nothing here is part of the monitor. It answers one question before we design
around it: OpenTable sits behind bot protection and GitHub runners are
datacenter IPs — the same combination that already forces the Instagram check
in this repo through Apify's residential proxies. If OpenTable blocks the
runner, a plain requests-based scraper is a dead end and we need a different
transport.

Run it from the "OpenTable Probe" workflow (workflow_dispatch), or locally with
`python scripts/probe_opentable.py` to compare a residential IP against CI.

It makes a handful of requests, not a flood — this is a feasibility check.
"""

import json
import re
import sys
import time

import requests

RESTAURANT_URL = "https://www.opentable.com/r/una-pizza-napoletana-new-york"

# A real browser UA. Not evasion — a default python-requests UA is rejected by
# most WAFs regardless of who's asking, which would tell us nothing about
# whether the IP itself is the problem.
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")

HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Upgrade-Insecure-Requests": "1",
}

# Substrings that mean "you got a challenge/denial page, not the restaurant".
# Covers the usual suspects: Akamai, PerimeterX/HUMAN, Cloudflare, DataDome.
CHALLENGE_MARKERS = [
    "pardon our interruption", "access denied", "captcha", "px-captcha",
    "cf-chl", "checking your browser", "unusual traffic", "are you a robot",
    "datadome", "bot detection", "request unsuccessful",
]

# Headers that identify the protection layer, when present.
WAF_HEADERS = [
    "server", "via", "x-cache", "cf-ray", "x-akamai-transformed",
    "x-datadome", "x-px", "set-cookie",
]


def banner(title: str) -> None:
    print(f"\n{'=' * 62}\n{title}\n{'=' * 62}")


def egress_ip() -> None:
    banner("1. Runner egress IP")
    for url in ("https://api.ipify.org?format=json", "https://ifconfig.me/all.json"):
        try:
            r = requests.get(url, timeout=15)
            print(f"  {url} -> {r.status_code} {r.text[:200]}")
            return
        except Exception as e:
            print(f"  {url} -> {type(e).__name__}: {e}")


def classify(body: str) -> list[str]:
    low = body.lower()
    return [m for m in CHALLENGE_MARKERS if m in low]


def fetch_restaurant_page() -> str | None:
    """GET the public restaurant page. This is the make-or-break request."""
    banner("2. GET the restaurant page")
    try:
        t0 = time.time()
        r = requests.get(RESTAURANT_URL, headers=HEADERS, timeout=30)
        dt = time.time() - t0
    except Exception as e:
        print(f"  BLOCKED/ERROR: {type(e).__name__}: {e}")
        return None

    print(f"  status={r.status_code}  bytes={len(r.content)}  {dt:.2f}s")
    print("  --- response headers of interest ---")
    for h in WAF_HEADERS:
        if h in r.headers:
            print(f"    {h}: {r.headers[h][:180]}")

    hits = classify(r.text)
    if hits:
        print(f"  !! CHALLENGE PAGE — matched {hits}")
        print(f"  --- first 400 chars ---\n{r.text[:400]}")
        return None
    if r.status_code != 200:
        print(f"  !! non-200; first 400 chars:\n{r.text[:400]}")
        return None

    # Sanity: does it actually look like the restaurant we asked for?
    looks_right = "una pizza" in r.text.lower()
    print(f"  page mentions 'una pizza': {looks_right}")
    print("  => restaurant page is READABLE from this IP")
    return r.text


def extract_ids(html: str) -> dict:
    """Pull the numeric restaurant id out of the page's embedded JSON.

    We need the rid to ask the availability endpoint anything. Several shapes
    have shipped over time, so try a few patterns rather than one brittle one.
    """
    banner("3. Extract restaurant id from embedded JSON")
    found = {}
    patterns = {
        "restaurantId": r'"restaurantId"\s*:\s*(\d+)',
        "rid": r'"rid"\s*:\s*(\d+)',
        "restaurant_id": r'"restaurant_id"\s*:\s*(\d+)',
    }
    for name, pat in patterns.items():
        m = re.search(pat, html)
        if m:
            found[name] = m.group(1)
            print(f"  {name} = {m.group(1)}")
    if not found:
        print("  !! no restaurant id found — page shape may have changed")
    # __NEXT_DATA__ presence tells us whether the useful JSON is inlined at all.
    print(f"  __NEXT_DATA__ present: {'__NEXT_DATA__' in html}")
    return found


def probe_availability(rid: str | None) -> None:
    """Try the endpoints the site's own frontend uses for slot availability."""
    banner("4. Availability endpoint")
    if not rid:
        print("  skipped — no restaurant id")
        return

    gql = "https://www.opentable.com/dapi/fe/gql?optype=query&opname=RestaurantsAvailability"
    headers = {**HEADERS, "Accept": "application/json",
               "Content-Type": "application/json", "Origin": "https://www.opentable.com",
               "Referer": RESTAURANT_URL}
    try:
        r = requests.post(gql, headers=headers, json={}, timeout=30)
        print(f"  POST dapi/fe/gql -> {r.status_code} bytes={len(r.content)}")
        print(f"  body[:400]: {r.text[:400]}")
    except Exception as e:
        print(f"  POST dapi/fe/gql -> {type(e).__name__}: {e}")


def main() -> int:
    print("OpenTable feasibility probe")
    print(f"target: {RESTAURANT_URL}")
    egress_ip()
    html = fetch_restaurant_page()
    if html is None:
        banner("VERDICT")
        print("  Restaurant page NOT readable from this IP.")
        print("  A plain requests scraper on GitHub Actions will not work.")
        return 1
    ids = extract_ids(html)
    probe_availability(ids.get("restaurantId") or ids.get("rid"))
    banner("VERDICT")
    print("  Restaurant page readable. See section 4 for whether slot data is.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
