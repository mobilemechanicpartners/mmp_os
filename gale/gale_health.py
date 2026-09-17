"""
One-shot health report for Gale, printed as a single line of JSON.

The n8n watchdog runs this over SSH and turns it into the nightly Slack
digest. Everything here is read-only — it never touches Tekmetric, sends
nothing, and changes no state, so it is always safe to run.
"""

import json
import os
import time
from datetime import datetime, timezone

import config

STATE_FILES = {
    "session_health": os.path.join(config.STATE_DIR, "session_health.json"),
    "last_run": os.path.join(config.STATE_DIR, "last_run.json"),
}


def _age_hours(path):
    if not os.path.exists(path):
        return None
    return round((time.time() - os.path.getmtime(path)) / 3600, 1)


def _load(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:  # noqa: BLE001 -- a missing or half-written file is not fatal
        return None


def main():
    report = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "session_age_hours": _age_hours(config.TEKMETRIC_STORAGE_STATE),
        "session_health": _load(STATE_FILES["session_health"]),
        "last_run": _load(STATE_FILES["last_run"]),
        "last_run_age_hours": _age_hours(STATE_FILES["last_run"]),
    }

    # How many ROs are sitting ready to invoice right now. A number that keeps
    # climbing night after night means Gale has quietly stopped working, even
    # if nothing ever errored.
    try:
        import ro_tracker
        import state

        ready = ro_tracker.get_invoiceable_drivetime_ros()
        report["ready_to_invoice"] = sum(
            1 for ro in ready if not state.is_completed(ro_tracker.ro_key(ro))
        )
    except Exception as e:  # noqa: BLE001 -- Sheets being down is itself worth reporting
        report["ready_to_invoice"] = None
        report["ready_error"] = str(e)[:200]

    print(json.dumps(report))


if __name__ == "__main__":
    main()
