"""Remembers the showtimes (and their sold-out status) we've already seen, so
we can alert only on *changes*: new showtimes appearing, or a sold-out show
freeing up.

state/seen.json shape:
    {"shows": {"The Odyssey|2026-07-21": {"11:00pm": false, "10:30am": false}}}
where the bool is sold_out.
"""

import json
import os
import tempfile
from pathlib import Path

STATE_FILE = Path(__file__).resolve().parent.parent / "state" / "seen.json"


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {"shows": {}}
    try:
        with open(STATE_FILE) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"shows": {}}
    data.setdefault("shows", {})  # tolerate older/empty files
    return data


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
