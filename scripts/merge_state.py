"""Merge this run's state into the on-disk state/seen.json.

Used by the workflow's commit step: because state is persisted by committing
state/seen.json back to the repo, two runs landing close together can race on
`git push`. To recover, we reset to the latest remote state and merge THIS
run's state into it (rather than overwriting), so no run's updates are lost —
which is what was causing duplicate alerts when a push got rejected.

Usage: python3 scripts/merge_state.py <our_state.json>
  - our_state.json : this run's state (saved before reset to origin/main)
  - state/seen.json: the remote state on disk (after `git reset --hard origin/main`)
The merged result is written back to state/seen.json.
"""

import json
import sys


def load(path: str) -> dict:
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


def main() -> None:
    our = load(sys.argv[1])
    remote = load("state/seen.json")

    # Union of (movie|date) -> {time: sold_out}. Our run's values win per-time
    # (it's the freshest read), but we keep any keys/times only remote has.
    shows = dict(remote.get("shows", {}))
    for key, times in our.get("shows", {}).items():
        shows[key] = {**shows.get(key, {}), **times}
    merged: dict = {"shows": shows}

    seen = set(remote.get("ig_seen", [])) | set(our.get("ig_seen", []))
    if seen:
        merged["ig_seen"] = sorted(seen)

    # Emails we've already alerted on (Dune Insider watch). Union like ig_seen
    # so a racing run's sends aren't dropped; both sides are bounded to the
    # recent search window, so the union stays bounded too.
    email_seen = set(remote.get("email_seen", [])) | set(our.get("email_seen", []))
    if email_seen:
        merged["email_seen"] = sorted(email_seen)

    # Scalar "most recent wins" fields. apify_token_index is a monotonic
    # counter (never reset), so max() keeps the rotation advancing across runs.
    for field in ("ig_last_check", "last_failure_alert", "amc_last_check",
                  "apify_token_index"):
        val = max(remote.get(field, 0), our.get(field, 0))
        if val:
            merged[field] = val

    # This run's view of the consecutive-failure streak is authoritative.
    if "amc_fail_streak" in our or "amc_fail_streak" in remote:
        merged["amc_fail_streak"] = our.get(
            "amc_fail_streak", remote.get("amc_fail_streak", 0))

    # Escalation-armed flags: once armed, stay armed (OR across both sides).
    armed = {}
    for src in (remote.get("escalation_armed", {}), our.get("escalation_armed", {})):
        for movie, flag in src.items():
            armed[movie] = armed.get(movie, False) or bool(flag)
    if armed:
        merged["escalation_armed"] = armed

    # Once the AMC API key is confirmed live, it stays live (OR).
    if remote.get("amc_api_confirmed") or our.get("amc_api_confirmed"):
        merged["amc_api_confirmed"] = True
    # Cached theatre id — keep whichever side has it.
    tid = our.get("amc_theatre_id") or remote.get("amc_theatre_id")
    if tid:
        merged["amc_theatre_id"] = tid

    with open("state/seen.json", "w") as f:
        json.dump(merged, f, indent=2)


if __name__ == "__main__":
    main()
