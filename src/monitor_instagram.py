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
from state import load_state, save_state

# Public web app id Instagram's own site sends with this endpoint.
IG_APP_ID = "936619743392459"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


def _apify_tokens() -> list[str]:
    """Apify API tokens in env-var order: APIFY_TOKEN, APIFY_TOKEN_2,
    APIFY_TOKEN_3. Supplying several free-tier keys lets us rotate across them
    (see check_instagram) and roughly triples our monthly credits. Empty/unset
    vars and duplicates are dropped; a lone APIFY_TOKEN still works as before.
    """
    tokens: list[str] = []
    for name in ("APIFY_TOKEN", "APIFY_TOKEN_2", "APIFY_TOKEN_3"):
        tok = os.environ.get(name, "").strip()
        if tok and tok not in tokens:
            tokens.append(tok)
    return tokens


def fetch_posts(username: str, tokens: list[str] | None = None) -> list[dict]:
    """Recent posts for a username: [{shortcode, timestamp, caption}].

    Uses Apify (residential proxies) when any APIFY_TOKEN* is set — required
    from CI, since Instagram blocks datacenter IPs. Falls back to Instagram's
    free direct endpoint otherwise (fine locally, usually blocked on CI).
    `tokens` is the rotated key order to try; defaults to env order.
    """
    if tokens is None:
        tokens = _apify_tokens()
    if tokens:
        return _fetch_via_apify(username, tokens)
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


def _fetch_via_apify(username: str, tokens: list[str]) -> list[dict]:
    """Try each token in order until one succeeds, so a single exhausted /
    rate-limited free-tier key falls through to the next instead of failing the
    whole scrape. `tokens` is already rotated by check_instagram so the starting
    key varies per run (load spreads evenly across all keys)."""
    last_err: Exception = RuntimeError("no Apify tokens configured")
    for i, token in enumerate(tokens):
        try:
            return _apify_call(username, token)
        except Exception as e:
            last_err = e
            if i < len(tokens) - 1:
                print(f"  · Apify key {i + 1}/{len(tokens)} failed ({e}); "
                      f"trying next")
    raise last_err


def _apify_call(username: str, token: str) -> list[dict]:
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
    the others.

    Rotates the Apify key each run when several are configured: a monotonic
    counter in state picks the starting key, so consecutive runs lead with
    different keys and the (limited free-tier) credit load spreads evenly.
    Remaining keys act as fallbacks within the run (see _fetch_via_apify).
    """
    tokens = _apify_tokens()
    if len(tokens) > 1:
        st = load_state()
        counter = st.get("apify_token_index", 0)
        idx = counter % len(tokens)
        tokens = tokens[idx:] + tokens[:idx]  # rotate so key #idx leads
        st["apify_token_index"] = counter + 1  # monotonic; merge keeps the max
        save_state(st)
        print(f"  · using Apify key {idx + 1}/{len(tokens)} (rotating)")

    out: list[dict] = []
    for username in INSTAGRAM_ACCOUNTS:
        try:
            out.extend(fetch_posts(username, tokens))
        except Exception as e:
            print(f"  ! Instagram @{username} failed: {e}")
    return out
