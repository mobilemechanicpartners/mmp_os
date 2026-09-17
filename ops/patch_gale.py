"""
One-time hardening patch for the Gale invoicing agent on gale-server.

Applies four fixes to the live code in ~/gale, each one traced to a real
overnight failure seen in logs/cron.log, logs/daily_sweep.log, or the Slack
alert history:

  1. tekmetric.py  — save the browser session back to disk on every run, so
     Tekmetric's rolling login never ages out unattended. This is the root
     cause of the recurring "session has expired" floods.
  2. tekmetric.py  — navigate with domcontentloaded + retries instead of a
     bare 30s "load" wait ("Page.goto: Timeout 30000ms exceeded", "Navigation
     ... interrupted by another navigation").
  3. gmail_client.py — retry Gmail on quota/5xx instead of letting a 403
     rateLimitExceeded end the run (and the failure alert with it).
  4. daily_sweep.py — refuse to start if another sweep is still running, and
     record a heartbeat of every run so a watchdog can tell whether Gale is
     actually alive.

Every edit asserts on its anchor and is a no-op if already applied, so this
is safe to re-run. Originals are backed up alongside as .bak_<timestamp>.
"""

import os
import shutil
import sys
import time

BASE = os.path.expanduser("~/gale")
STAMP = time.strftime("%Y%m%d_%H%M%S")
_changed = {}


def _read(name):
    with open(os.path.join(BASE, name)) as f:
        return f.read()


def _write(name, text):
    path = os.path.join(BASE, name)
    shutil.copy2(path, f"{path}.bak_{STAMP}")
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(text)
    os.replace(tmp, path)


def patch(src, old, new, label, marker=None):
    """Replace `old` with `new` exactly once.

    `marker` is a short string unique to the patched result, used to detect
    "already applied" for edits where `old` is still a substring of `new`
    (adding an import, appending to a block). Without it, re-running would
    happily apply those a second time.
    """
    if (marker or new) in src:
        print(f"  skip   {label} (already applied)")
        return src
    count = src.count(old)
    if count != 1:
        print(f"  FAILED {label}: anchor matched {count} times, expected 1")
        sys.exit(1)
    print(f"  patch  {label}")
    return src.replace(old, new, 1)


# ---------------------------------------------------------------------------
# tekmetric.py
# ---------------------------------------------------------------------------
print("tekmetric.py")
src = _read("tekmetric.py")

src = patch(
    src,
    "def _looks_like_login_page(page) -> bool:",
    '''def _goto(page, url, attempts=3):
    """Tekmetric's cold SPA loads and its login redirect regularly blow past
    a 30s "load" wait, which used to fail the whole RO. Waiting for
    domcontentloaded instead — and retrying twice — turns those into a
    non-event."""
    last_error = None
    for attempt in range(attempts):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            return
        except Exception as e:  # noqa: BLE001 -- retried below, re-raised at the end
            last_error = e
            page.wait_for_timeout(2000 * (attempt + 1))
    raise TekmetricError(f"Could not load {url} after {attempts} attempts: {last_error}")


def _looks_like_login_page(page) -> bool:''',
    "add _goto() with retries",
    marker="def _goto(page, url, attempts=3):",
)

src = patch(
    src,
    "        self.page = self.context.new_page()\n        self.page.goto(config.TEKMETRIC_URL)",
    "        self.page = self.context.new_page()\n        _goto(self.page, config.TEKMETRIC_URL)",
    "use _goto on session open",
)

src = patch(
    src,
    "        def _attempt() -> bool:\n            page.goto(config.TEKMETRIC_URL)",
    "        def _attempt() -> bool:\n            _goto(page, config.TEKMETRIC_URL)",
    "use _goto on shop switch",
)

src = patch(
    src,
    """            )
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.context:
            self.context.close()""",
    '''            )
        self.save_session()
        return self

    def save_session(self):
        """Write the browser's cookies back to disk after a good load.

        Tekmetric issues a rolling session: it stays valid as long as it keeps
        being used, but the saved copy on disk was never updated, so it aged
        out every few days and a human had to log in by hand. Saving it back
        on every run is what makes the login survive indefinitely.

        Written via a temp file and an atomic replace — a crash mid-write
        would otherwise leave a truncated session file, which is worse than
        an expired one.
        """
        if not self.context:
            return
        try:
            tmp = config.TEKMETRIC_STORAGE_STATE + ".tmp"
            self.context.storage_state(path=tmp)
            os.replace(tmp, config.TEKMETRIC_STORAGE_STATE)
        except Exception:  # noqa: BLE001 -- session upkeep must never break a run
            pass

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.save_session()
        if self.context:
            self.context.close()''',
    "save session on open and close",
)

_changed["tekmetric.py"] = src


# ---------------------------------------------------------------------------
# gmail_client.py
# ---------------------------------------------------------------------------
print("gmail_client.py")
src = _read("gmail_client.py")

src = patch(
    src,
    "import base64\nimport mimetypes\nimport os\n",
    "import base64\nimport mimetypes\nimport os\nimport time\n",
    "import time",
    marker="import os\nimport time\n",
)

src = patch(
    src,
    'def _service():\n    return build("gmail", "v1", credentials=_get_credentials())',
    '''def _service():
    return build("gmail", "v1", credentials=_get_credentials())


# Gmail enforces a per-minute quota that one sweep can trip: reading the
# DriveTime confirmation inbox burns units, and then an invoice send comes
# back 403 rateLimitExceeded. Unretried, that ended the entire run — and the
# "Gale failed" alert, being another Gmail send, died the same way, so the
# run failed silently. Every API call goes through _execute() now.
_RETRYABLE_STATUS = {403, 429, 500, 502, 503, 504}
_RETRY_DELAYS = (5, 15, 45, 90)


def _execute(request):
    from googleapiclient.errors import HttpError

    for delay in _RETRY_DELAYS + (None,):
        try:
            return request.execute()
        except HttpError as e:
            status = getattr(getattr(e, "resp", None), "status", None)
            if status not in _RETRYABLE_STATUS or delay is None:
                raise
            time.sleep(delay)''',
    "add _execute() retry wrapper",
    marker="_RETRYABLE_STATUS",
)

for old, new, label in [
    (
        'draft = service.users().drafts().create(userId="me", body={"message": raw_message}).execute()',
        'draft = _execute(service.users().drafts().create(userId="me", body={"message": raw_message}))',
        "retry create_draft",
    ),
    (
        'sent = service.users().drafts().send(userId="me", body={"id": draft_id}).execute()',
        'sent = _execute(service.users().drafts().send(userId="me", body={"id": draft_id}))',
        "retry send_draft",
    ),
    (
        'sent = service.users().messages().send(userId="me", body=raw_message).execute()',
        'sent = _execute(service.users().messages().send(userId="me", body=raw_message))',
        "retry send_message",
    ),
    (
        'msg = service.users().messages().get(userId="me", id=message_id, format="minimal").execute()',
        'msg = _execute(service.users().messages().get(userId="me", id=message_id, format="minimal"))',
        "retry verify_sent",
    ),
]:
    src = patch(src, old, new, label)

_changed["gmail_client.py"] = src


# ---------------------------------------------------------------------------
# daily_sweep.py
# ---------------------------------------------------------------------------
print("daily_sweep.py")
src = _read("daily_sweep.py")

src = patch(
    src,
    "import logging\nimport os\nimport sys\nfrom datetime import datetime",
    "import fcntl\nimport json\nimport logging\nimport os\nimport sys\nfrom datetime import datetime",
    "imports",
    marker="import fcntl",
)

src = patch(
    src,
    'log = logging.getLogger("daily_sweep")',
    '''log = logging.getLogger("daily_sweep")

LOCK_PATH = os.path.join(config.STATE_DIR, "gale.lock")
HEARTBEAT_PATH = os.path.join(config.STATE_DIR, "last_run.json")

# Filled in as the run progresses; written to HEARTBEAT_PATH on the way out so
# the watchdog can tell a healthy quiet night from a sweep that never ran.
RUN_STATS = {"status": "ok"}


def _acquire_lock():
    """Only one sweep at a time, ever.

    The 6pm run has been seen still working at 1am, which put it head-to-head
    with the next scheduled run on the same ROs — two browsers in Tekmetric
    and two invoices to DriveTime, with nothing but a state file in between.
    The lock is held by the process, so it is released even on a hard kill.
    """
    os.makedirs(config.STATE_DIR, exist_ok=True)
    handle = open(LOCK_PATH, "w")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return None
    handle.write(f"{os.getpid()} {datetime.now().isoformat()}\\n")
    handle.flush()
    return handle


def write_heartbeat():
    try:
        os.makedirs(config.STATE_DIR, exist_ok=True)
        payload = {"finished_at": datetime.now().isoformat(), **RUN_STATS}
        tmp = HEARTBEAT_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(payload, f, indent=2, default=str)
        os.replace(tmp, HEARTBEAT_PATH)
    except Exception:  # noqa: BLE001 -- reporting must never break the run
        log.warning("Could not write the run heartbeat.", exc_info=True)''',
    "lock + heartbeat helpers",
    marker="LOCK_PATH",
)

src = patch(
    src,
    '''def main(limit=None):
    os.makedirs(config.LOGS_DIR, exist_ok=True)
    log.info("Starting daily sweep...")''',
    '''def main(limit=None):
    os.makedirs(config.LOGS_DIR, exist_ok=True)

    lock = _acquire_lock()
    if lock is None:
        RUN_STATS["status"] = "skipped_already_running"
        log.warning("Another sweep is still running — exiting without touching any ROs.")
        return

    log.info("Starting daily sweep...")''',
    "acquire lock",
)

src = patch(
    src,
    '        log.info("Sent Tekmetric-login-expired alert to Reuben. Stopping this run early (nothing else attempted).")\n        return',
    '        RUN_STATS["status"] = "login_expired"\n        log.info("Sent Tekmetric-login-expired alert to Reuben. Stopping this run early (nothing else attempted).")\n        return',
    "record login_expired",
    marker='RUN_STATS["status"] = "login_expired"',
)

src = patch(
    src,
    """            skipped_items = state.pop_all_skipped()
            report.send_session_report(sent_items, skipped_items=skipped_items, failed_items=failed_items)""",
    """            skipped_items = state.pop_all_skipped()
            RUN_STATS.update(
                sent=len(sent_items), failed=len(failed_items), skipped=len(skipped_items)
            )
            report.send_session_report(sent_items, skipped_items=skipped_items, failed_items=failed_items)""",
    "record run counts",
)

src = patch(
    src,
    '''    try:
        main(limit=_limit)
    except Exception as _e:
        log.error(f"Daily sweep crashed: {_e}", exc_info=True)
        _alert_reuben_of_failure(_e)
        sys.exit(1)''',
    '''    try:
        main(limit=_limit)
    except Exception as _e:
        log.error(f"Daily sweep crashed: {_e}", exc_info=True)
        RUN_STATS["status"] = "crashed"
        RUN_STATS["error"] = str(_e)[:300]
        write_heartbeat()
        _alert_reuben_of_failure(_e)
        sys.exit(1)
    write_heartbeat()''',
    "write heartbeat on exit",
)

_changed["daily_sweep.py"] = src


# ---------------------------------------------------------------------------
for name, text in _changed.items():
    _write(name, text)
print(f"\nWrote {len(_changed)} file(s), backups stamped .bak_{STAMP}")
