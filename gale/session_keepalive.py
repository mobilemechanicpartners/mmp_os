"""
Keeps the saved Tekmetric login alive, so Gale never wakes up logged out.

Tekmetric's session is rolling: it stays valid while it keeps being used, but
the copy on disk was only ever written by hand, so it aged out every few days
and every RO in that night's run failed with "the saved session has expired".

Opening a session now re-saves it (see TekmetricSession.save_session), so this
script is simply "open Tekmetric, prove we're still logged in, save". Run it
on a timer between sweeps and the login stays good indefinitely.

Writes state/session_health.json for the n8n watchdog:
    {"status": "ok" | "expired" | "error", "detail": "...", "checked_at": "..."}
Exit code is 0 when healthy, non-zero otherwise.
"""

import json
import os
import sys
from datetime import datetime, timezone

import config
from tekmetric import TekmetricSession, LoginExpiredError

HEALTH_PATH = os.path.join(config.STATE_DIR, "session_health.json")


def _report(status: str, detail: str = "") -> None:
    payload = {
        "status": status,
        "detail": detail[:300],
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        os.makedirs(config.STATE_DIR, exist_ok=True)
        tmp = HEALTH_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp, HEALTH_PATH)
    except Exception:  # noqa: BLE001 -- still print, so a caller sees the result
        pass
    print(json.dumps(payload))


def main() -> int:
    try:
        with TekmetricSession(headless=True):
            pass  # opening and closing both re-save the refreshed session
    except LoginExpiredError as e:
        _report("expired", str(e))
        return 1
    except Exception as e:  # noqa: BLE001 -- a flake here is not an expired login
        _report("error", str(e))
        return 2
    _report("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
