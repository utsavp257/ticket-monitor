"""Tracks which alerts we've already sent so we don't spam on every run.

The dedup *key* (built in main.py) deliberately includes the date and the
first-show time, so if an earlier show gets added we'll alert again — but a
steady-state listing won't re-notify.
"""

import json
import os
import tempfile
from pathlib import Path

STATE_FILE = Path(__file__).resolve().parent.parent / "state" / "seen.json"


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {"seen": []}
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        # Corrupt/empty file shouldn't wedge the monitor.
        return {"seen": []}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    # Atomic write so an interrupted run can't corrupt the file.
    fd, tmp = tempfile.mkstemp(dir=STATE_FILE.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp, STATE_FILE)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def already_seen(key: str) -> bool:
    return key in load_state()["seen"]


def mark_seen(key: str) -> None:
    state = load_state()
    if key not in state["seen"]:
        state["seen"].append(key)
    save_state(state)
