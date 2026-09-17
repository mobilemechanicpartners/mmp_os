"""
Simple JSON-backed state store.

Two files:
  - gale_state.json     : permanent record of every RO Gale has ever fully
                           completed (invoiced + posted to A/R). Used so a
                           row that syncs slowly in the RO Tracker never
                           gets double-invoiced.
  - pending_batch.json   : the *current* batch of drafted-but-not-yet-sent
                           invoices, waiting on Reuben's approval.
"""

import json
import os
from datetime import datetime, timezone

import config


def _load(path, default):
    if not os.path.exists(path):
        return default
    with open(path, "r") as f:
        return json.load(f)


def _save(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(data, f, indent=2, default=str)
    os.replace(tmp_path, path)


def load_state():
    return _load(config.STATE_FILE, {"completed_ros": {}})


def save_state(state):
    _save(config.STATE_FILE, state)


def is_completed(ro_key):
    state = load_state()
    return ro_key in state.get("completed_ros", {})


def mark_completed(ro_key, info):
    state = load_state()
    state.setdefault("completed_ros", {})[ro_key] = {
        **info,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    save_state(state)


def load_pending_batch():
    return _load(config.PENDING_BATCH_FILE, {"created_at": None, "items": []})


def save_pending_batch(batch):
    _save(config.PENDING_BATCH_FILE, batch)


def clear_pending_batch():
    if os.path.exists(config.PENDING_BATCH_FILE):
        os.remove(config.PENDING_BATCH_FILE)


def _load_skipped():
    return _load(config.SKIPPED_LOG_FILE, {"items": []})


def append_skipped(item: dict):
    data = _load_skipped()
    data["items"].append(item)
    _save(config.SKIPPED_LOG_FILE, data)


def pop_all_skipped():
    """Returns everything queued since the last report, and clears the queue."""
    data = _load_skipped()
    if os.path.exists(config.SKIPPED_LOG_FILE):
        os.remove(config.SKIPPED_LOG_FILE)
    return data.get("items", [])
