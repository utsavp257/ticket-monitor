"""Watch Instagram accounts for new posts (they often post when tickets go
live). Uses Instagram's logged-out web_profile_info JSON endpoint — no login,
no third party, no API key.

Caveat: Instagram aggressively rate-limits/blocks datacenter IPs, so this is
less reliable than the AMC check, especially from CI runners. Failures are
isolated and logged; they never crash the rest of the monitor.
"""

from __future__ import annotations

import os

import requests

from config import INSTAGRAM_ACCOUNTS, APIFY_ACTOR

# Public web app id Instagram's own site sends with this endpoint.
IG_APP_ID = "936619743392459"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


def fetch_posts(username: str) -> list[dict]:
    """Recent posts for a username: [{shortcode, timestamp, caption}].

    Uses Apify (residential proxies) when APIFY_TOKEN is set — required from CI,
    since Instagram blocks datacenter IPs. Falls back to Instagram's free direct
    endpoint otherwise (fine locally, usually blocked on CI).
    """
    if os.environ.get("APIFY_TOKEN"):
        return _fetch_via_apify(username)
    return _fetch_direct(username)


def _fetch_direct(username: str) -> list[dict]:
    resp = requests.get(
        "https://www.instagram.com/api/v1/users/web_profile_info/",
        params={"username": username},
        headers={
            "User-Agent": UA,
            "x-ig-app-id": IG_APP_ID,
            "Accept": "application/json",
        },
        timeout=20,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code} (likely IP-blocked)")
    user = resp.json()["data"]["user"]
    posts = []
    for edge in user["edge_owner_to_timeline_media"]["edges"]:
        node = edge["node"]
        caption_edges = node["edge_media_to_caption"]["edges"]
        caption = caption_edges[0]["node"]["text"] if caption_edges else ""
        posts.append({
            "username": username,
            "shortcode": node["shortcode"],
            "timestamp": node["taken_at_timestamp"],
            "caption": caption,
        })
    return posts


def _fetch_via_apify(username: str) -> list[dict]:
    token = os.environ["APIFY_TOKEN"]
    resp = requests.post(
        f"https://api.apify.com/v2/acts/{APIFY_ACTOR}/run-sync-get-dataset-items",
        params={"token": token},
        json={
            "directUrls": [f"https://www.instagram.com/{username}/"],
            "resultsType": "posts",
            "resultsLimit": 12,
            "addParentData": False,
        },
        timeout=180,
    )
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"Apify HTTP {resp.status_code}: {resp.text[:200]}")
    posts = []
    for item in resp.json():
        # Field names vary slightly across actor versions — be tolerant.
        shortcode = item.get("shortCode") or item.get("shortcode")
        if not shortcode:
            continue
        posts.append({
            "username": username,
            "shortcode": shortcode,
            "timestamp": item.get("timestamp") or item.get("takenAt") or "",
            "caption": item.get("caption") or "",
        })
    return posts


def check_instagram() -> list[dict]:
    """Posts across all configured accounts. One account failing doesn't stop
    the others."""
    out: list[dict] = []
    for username in INSTAGRAM_ACCOUNTS:
        try:
            out.extend(fetch_posts(username))
        except Exception as e:
            print(f"  ! Instagram @{username} failed: {e}")
    return out
