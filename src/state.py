import json
from pathlib import Path

STATE_FILE = Path("state/seen.json")


def load_state():
    if not STATE_FILE.exists():
        return {"seen": []}

    with open(STATE_FILE) as f:
        return json.load(f)


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def already_seen(item):
    state = load_state()
    return item in state["seen"]


def mark_seen(item):
    state = load_state()

    if item not in state["seen"]:
        state["seen"].append(item)

    save_state(state)
