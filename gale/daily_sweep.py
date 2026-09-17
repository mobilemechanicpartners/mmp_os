"""
Run once a day (cron). Finds DriveTime ROs that are Complete/Balance Due
and pulls each invoice PDF from Tekmetric.

Two modes, controlled by config.AUTO_SEND:
  - AUTO_SEND = False (draft-and-wait): creates a Gmail DRAFT for each
    (To: invoices@drivetime.com, PDF attached) so Reuben can review and
    send them himself. Run check_and_finalize.py afterwards to detect
    which drafts he's sent, post those ROs to A/R, and report.
  - AUTO_SEND = True (full autonomy): sends each invoice immediately and
    posts it to A/R in the same run, then emails the session summary
    report directly — no approval step, no check_and_finalize needed.

DriveTime Inspection Center Houston ("Recon") ROs go through the exact
same pipeline as every other DriveTime RO, but invoice_recipient() below
routes their email to config.RECON_INVOICE_EMAILS instead of the standard
config.DRIVETIME_INVOICE_EMAIL.
"""

import fcntl
import json
import logging
import os
import sys
from datetime import datetime

import config
import gale_log
import gmail_client
import report
import ro_tracker
import state
import track_drivetime_confirmations
from tekmetric import TekmetricSession, TekmetricError, LoginExpiredError

def invoice_recipient(ro: dict) -> str:
    """Which address(es) an RO's invoice should go to. DriveTime Recon
    (DriveTime Inspection Center Houston) routes to a distinct recipient
    list instead of the standard DriveTime invoicing inbox."""
    name = (ro.get("customer_name") or "").strip().lower()
    if name in config.CUSTOMER_NAME_RECON_EXACT:
        return ", ".join(config.RECON_INVOICE_EMAILS)
    return config.DRIVETIME_INVOICE_EMAIL


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(config.LOGS_DIR, "daily_sweep.log")),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("daily_sweep")

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
    handle.write(f"{os.getpid()} {datetime.now().isoformat()}\n")
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
        log.warning("Could not write the run heartbeat.", exc_info=True)


def build_subject(ro: dict) -> str:
    """Subject format per Reuben: RO #<num> — <customer>, <year> <model>
    (VIN <vin>, Claim <claim>)."""
    return (
        f"RO #{ro['ro_number']} — {ro['customer_name']}, "
        f"{ro['year']} {ro['model']} (VIN {ro['vin']}, Claim {ro['claim_number']})"
    )


def build_body(ro: dict) -> str:
    return (
        f"Invoice attached for RO #{ro['ro_number']} — {ro['customer_name']}, "
        f"{ro['year']} {ro['model']} (VIN {ro['vin']}, Claim {ro['claim_number']}).\n\n"
        f"-- \nReuben Fine\nMobile Mechanic Partners\n"
        f"reuben@mobilemechanicpartners.com\n(949) 633-0805"
    )


_FIX_RO_20750_MARKER = os.path.join(config.STATE_DIR, ".fixed_ro_20750")
_FIX_RO_20750_ATTEMPTS = os.path.join(config.STATE_DIR, ".fixed_ro_20750_attempts")
_FIX_RO_20750_MAX_ATTEMPTS = 3


def _fix_ro_20750_once(tek):
    """One-time self-heal: RO #20750 (DriveTime Recon, Houston) was sent on
    9/1 during setup of the Recon workflow but never got posted to A/R or
    logged, because it wasn't sent through the normal sweep. state/gale_state.json
    was already patched by hand so this RO is never re-invoiced; this just
    finishes the job (Tekmetric A/R + spreadsheet) automatically the next
    time the sweep runs, so Reuben doesn't have to do anything by hand.
    Runs once -- deletes itself via a marker file once it succeeds."""
    if os.path.exists(_FIX_RO_20750_MARKER):
        return

    # This has to give up eventually. The RO is no longer findable by search,
    # so every run was re-attempting it, burning half a minute and dropping a
    # traceback into the log that looks like a real failure. After a few tries
    # we stop and say so plainly, rather than retrying forever in silence.
    attempts = 0
    if os.path.exists(_FIX_RO_20750_ATTEMPTS):
        try:
            attempts = int(open(_FIX_RO_20750_ATTEMPTS).read().strip() or 0)
        except ValueError:
            attempts = 0
    if attempts >= _FIX_RO_20750_MAX_ATTEMPTS:
        return
    with open(_FIX_RO_20750_ATTEMPTS, 'w') as f:
        f.write(str(attempts + 1))
    if attempts + 1 >= _FIX_RO_20750_MAX_ATTEMPTS:
        log.warning(
            'Giving up on the one-time A/R fix for RO #20750 after '
            f'{attempts + 1} attempts - it cannot be found in Tekmetric search. '
            'Check that RO by hand; nothing else is affected.'
        )
    try:
        tek.select_shop("MMP Houston")
        tek.open_repair_order("20750")
        tek.post_to_ar()
        ro = {
            "ro_number": "20750",
            "shop_name": "MMP Houston",
            "customer_name": "Drivetime Inspection Center Houston",
            "claim_number": "",
            "total_invoice": "",
        }
        gale_log.log_ro(ro, "Sent & Posted to A/R", "Recon invoice sent 9/1; A/R posting completed automatically by Gale on a later run")
        gale_log.log_ar(ro)
        with open(_FIX_RO_20750_MARKER, "w") as f:
            f.write("done")
        log.info("RO #20750 (Recon): posted to A/R and logged (one-time fix).")
    except Exception:  # noqa: BLE001 -- don't let this block the real sweep; it'll retry next run
        log.exception("One-time fix for RO #20750 failed -- will retry on the next sweep.")


def main(limit=None):
    os.makedirs(config.LOGS_DIR, exist_ok=True)

    lock = _acquire_lock()
    if lock is None:
        RUN_STATS["status"] = "skipped_already_running"
        log.warning("Another sweep is still running — exiting without touching any ROs.")
        return

    log.info("Starting daily sweep...")

    # Check Gmail for DriveTime "processed" and Tipalti payment confirmation
    # emails and fill in the Processed/Paid columns on the spreadsheet.
    # Never lets a Gmail/Sheets hiccup here stop the actual invoicing below.
    try:
        track_drivetime_confirmations.main()
    except Exception:  # noqa: BLE001
        log.exception("DriveTime confirmation tracking failed (non-fatal, continuing sweep).")

    candidates = ro_tracker.get_invoiceable_drivetime_ros()
    log.info(f"RO Tracker returned {len(candidates)} DriveTime ROs ready to invoice.")

    pending = state.load_pending_batch()
    already_pending_keys = {item["ro_key"] for item in pending.get("items", [])}

    # Filter down to actual work BEFORE applying the test-mode limit, so
    # --limit means "the next N not-yet-handled ROs", not "the first N
    # in the sheet regardless of whether they're already done."
    todo = [
        ro for ro in candidates
        if not state.is_completed(ro_tracker.ro_key(ro))
        and ro_tracker.ro_key(ro) not in already_pending_keys
    ]
    if limit is not None:
        todo = todo[:limit]
        log.info(f"TEST MODE: limiting this run to the next {limit} RO(s).")

    new_items = []      # draft-and-wait mode
    sent_items = []      # auto-send mode: sent + posted to A/R
    failed_items = []    # auto-send mode: sent but A/R post failed
    skipped = []

    try:
        tek_session = TekmetricSession(headless=True)
        tek_session.__enter__()
    except LoginExpiredError as e:
        log.error(str(e))
        gmail_client.send_message(
            to=config.GMAIL_SENDER,
            subject="Gale needs you: Tekmetric login expired",
            body_text=(
                "Gale tried to run today's DriveTime invoicing but your saved "
                "Tekmetric login has expired.\n\n"
                "To fix it: double-click 'Refresh Tekmetric Login.command' in "
                "the Gale Invoicing Agent folder, log in to Tekmetric as you "
                "normally do, then come back and press Enter in that window.\n\n"
                "Nothing was skipped or double-invoiced -- Gale didn't touch "
                "any ROs this run and will pick everything back up "
                "automatically on its next scheduled run once you're logged "
                "back in."
            ),
        )
        RUN_STATS["status"] = "login_expired"
        log.info("Sent Tekmetric-login-expired alert to Reuben. Stopping this run early (nothing else attempted).")
        return

    tek = tek_session
    try:
        _fix_ro_20750_once(tek)

        for ro in todo:
            key = ro_tracker.ro_key(ro)
            try:
                tek.select_shop(ro["shop_name"])
                tek.open_repair_order(ro["ro_number"])
                pdf_path = tek.download_invoice_pdf(ro["ro_number"])

                subject = build_subject(ro)
                body = build_body(ro)

                if config.AUTO_SEND:
                    gmail_client.send_message(
                        to=invoice_recipient(ro),
                        subject=subject,
                        body_text=body,
                        attachment_path=pdf_path,
                    )
                    log.info(f"Sent invoice for RO #{ro['ro_number']} ({ro['customer_name']}).")
                    try:
                        tek.post_to_ar()
                        state.mark_completed(key, {
                            "ro_number": ro["ro_number"],
                            "customer_name": ro["customer_name"],
                            "claim_number": ro["claim_number"],
                            "total_invoice": ro["total_invoice"],
                        })
                        sent_items.append({
                            "ro_number": ro["ro_number"],
                            "customer_name": ro["customer_name"],
                            "claim_number": ro["claim_number"],
                            "total_invoice": ro["total_invoice"],
                        })
                        log.info(f"RO #{ro['ro_number']} posted to A/R and marked complete.")
                        gale_log.log_ro(ro, "Sent & Posted to A/R")
                        gale_log.log_ar(ro)
                    except TekmetricError as e:
                        failed_items.append({
                            "ro_number": ro["ro_number"],
                            "customer_name": ro["customer_name"],
                            "reason": f"Invoice was sent but A/R posting failed: {e}",
                        })
                        log.error(f"RO #{ro['ro_number']}: sent but A/R post failed: {e}")
                        gale_log.log_ro(ro, "Sent, A/R Post Failed", str(e))
                else:
                    draft_id = gmail_client.create_draft(
                        to=invoice_recipient(ro),
                        subject=subject,
                        body_text=body,
                        attachment_path=pdf_path,
                    )
                    new_items.append({
                        "ro_key": key,
                        "ro_number": ro["ro_number"],
                        "shop_name": ro["shop_name"],
                        "customer_name": ro["customer_name"],
                        "claim_number": ro["claim_number"],
                        "total_invoice": ro["total_invoice"],
                        "subject": subject,
                        "draft_id": draft_id,
                        "pdf_path": pdf_path,
                    })
                    log.info(f"Drafted invoice for RO #{ro['ro_number']} ({ro['customer_name']}).")
                    gale_log.log_ro(ro, "Drafted (awaiting approval)")

            except TekmetricError as e:
                item = {"ro_number": ro["ro_number"], "customer_name": ro["customer_name"], "reason": str(e)}
                skipped.append(item)
                state.append_skipped(item)
                log.error(f"RO #{ro['ro_number']}: {e}")
                gale_log.log_ro(ro, "Error", str(e))
            except Exception as e:  # noqa: BLE001 — log and keep going, one bad RO shouldn't kill the run
                item = {"ro_number": ro["ro_number"], "customer_name": ro["customer_name"], "reason": f"Unexpected error: {e}"}
                skipped.append(item)
                state.append_skipped(item)
                log.exception(f"RO #{ro['ro_number']}: unexpected error")
                gale_log.log_ro(ro, "Error", f"Unexpected error: {e}")
    finally:
        tek_session.__exit__(None, None, None)

    if config.AUTO_SEND:
        if sent_items or failed_items or skipped:
            skipped_items = state.pop_all_skipped()
            RUN_STATS.update(
                sent=len(sent_items), failed=len(failed_items), skipped=len(skipped_items)
            )
            report.send_session_report(sent_items, skipped_items=skipped_items, failed_items=failed_items)
            log.info(
                f"Session report sent. {len(sent_items)} sent+posted, "
                f"{len(skipped_items)} skipped, {len(failed_items)} failed."
            )
        else:
            log.info("Nothing to invoice today.")
        return

    if new_items:
        pending.setdefault("items", []).extend(new_items)
        pending["created_at"] = pending.get("created_at") or datetime.now().isoformat()
        state.save_pending_batch(pending)

        subject = f"Gale — {len(new_items)} DriveTime invoice draft(s) ready for your review"
        body_lines = [
            "The following invoices are drafted in your Gmail Drafts folder, "
            "attached and addressed to invoices@drivetime.com. Review and hit "
            "Send on each one you approve — Gale will pick up the rest "
            "(posting to A/R + the summary report) automatically once you do.",
            "",
        ]
        for item in new_items:
            body_lines.append(f"  - {item['subject']}")
        gmail_client.send_message(config.GMAIL_SENDER, subject, "\n".join(body_lines))
        log.info(f"Notified Reuben about {len(new_items)} new draft(s).")
    else:
        log.info("No new invoices to draft today.")

    if skipped:
        log.warning(f"{len(skipped)} RO(s) skipped due to errors: {skipped}")


def _alert_reuben_of_failure(exc: Exception):
    """Last-resort safety net: if the sweep crashes with something the
    per-RO error handling never got a chance to catch (e.g. a Sheets
    outage that outlasted our retries), email Reuben directly rather
    than failing silently with nobody watching."""
    try:
        import traceback
        tb = traceback.format_exc()
        gmail_client.send_message(
            to=config.GMAIL_SENDER,
            subject="Gale — Daily sweep FAILED (needs a look)",
            body_text=(
                "Gale's daily sweep hit an error it couldn't recover from "
                "and did not finish this run. No invoices were sent or "
                "skipped as a result of this run.\n\n"
                f"Error: {exc}\n\n{tb}"
            ),
        )
    except Exception:
        log.error("Also failed to send the failure alert email.", exc_info=True)


if __name__ == "__main__":
    _limit = int(sys.argv[1]) if len(sys.argv) > 1 else None
    try:
        main(limit=_limit)
    except Exception as _e:
        log.error(f"Daily sweep crashed: {_e}", exc_info=True)
        RUN_STATS["status"] = "crashed"
        RUN_STATS["error"] = str(_e)[:300]
        write_heartbeat()
        _alert_reuben_of_failure(_e)
        sys.exit(1)
    write_heartbeat()
