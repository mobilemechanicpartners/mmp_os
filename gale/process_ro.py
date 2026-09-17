"""
process_ro.py — single-RO entrypoint for the n8n-orchestrated Gale.

n8n calls this once per DriveTime RO that's Complete/Balance Due:

    python3 process_ro.py '{"shop": "...", "customer": "...", "ro_number": "...",
                             "year": "...", "model": "...", "vin": "...",
                             "claim_number": "...", "total_invoice": "..."}'

It reuses the same tekmetric.py / gmail_client.py / state.py / config.py
that daily_sweep.py already used on Reuben's Mac — this is not a rewrite,
just a thin per-RO wrapper so n8n can drive one RO at a time and react to
the outcome of each individually.

Prints exactly ONE line of JSON to stdout as the last thing it does, always:
    {"status": "success"}
    {"status": "success", "note": "already posted to A/R, skipped re-sending"}
    {"status": "error", "errorType": "login_expired", "message": "..."}
    {"status": "error", "errorType": "selector_error", "message": "..."}
    {"status": "error", "errorType": "other", "message": "..."}

Exit code is 0 on success, 1 on any error — n8n's SSH node gets both the
code and the stdout, so it can react even if JSON parsing ever fails.
"""

import json
import sys

import config
import gmail_client
import state
from tekmetric import (
    TekmetricSession,
    TekmetricError,
    LoginExpiredError,
    ShopSwitchError,
)


def _emit(payload: dict, exit_code: int):
    print(json.dumps(payload))
    sys.exit(exit_code)


def main():
    if len(sys.argv) < 2:
        _emit({"status": "error", "errorType": "other", "message": "No RO payload passed on argv[1]."}, 1)

    try:
        ro = json.loads(sys.argv[1])
    except json.JSONDecodeError as e:
        _emit({"status": "error", "errorType": "other", "message": f"Bad JSON payload: {e}"}, 1)

    ro_key = f"{ro.get('shop')}|{ro.get('ro_number')}"

    if state.is_completed(ro_key):
        _emit({"status": "success", "note": "already marked completed in gale_state.json, skipped"}, 0)

    try:
        with TekmetricSession(headless=True) as tek:
            tek.select_shop(ro["shop"])
            tek.open_repair_order(str(ro["ro_number"]))

            if tek.is_in_ar():
                state.mark_completed(ro_key, {"ro_number": ro["ro_number"], "note": "was already In A/R on Tekmetric"})
                _emit({"status": "success", "note": "RO was already In A/R on Tekmetric, skipped re-sending"}, 0)

            pdf_path = tek.download_invoice_pdf(str(ro["ro_number"]))

            name = (ro.get("customer") or "").strip().lower()
            recon_names = {n.lower() for n in getattr(config, "CUSTOMER_NAME_RECON_EXACT", set())}
            if name in recon_names:
                to_addr = ", ".join(config.RECON_INVOICE_EMAILS)
            else:
                to_addr = config.DRIVETIME_INVOICE_EMAIL

            subject = (
                f"RO #{ro['ro_number']} — {ro['customer']}, "
                f"{ro.get('year', '')} {ro.get('model', '')} "
                f"(VIN {ro.get('vin', '')}, Claim {ro.get('claim_number', '')})"
            )
            body = (
                f"Invoice attached for RO #{ro['ro_number']} — {ro['customer']}, "
                f"{ro.get('year', '')} {ro.get('model', '')} "
                f"(VIN {ro.get('vin', '')}, Claim {ro.get('claim_number', '')}).\n\n"
                f"-- \nReuben Fine\nMobile Mechanic Partners\n"
                f"reuben@mobilemechanicpartners.com\n(949) 633-0805"
            )
            gmail_client.send_message(to_addr, subject, body, attachment_path=pdf_path)

            tek.post_to_ar()

            state.mark_completed(ro_key, {"ro_number": ro["ro_number"]})
            _emit({"status": "success"}, 0)

    except LoginExpiredError as e:
        _emit({"status": "error", "errorType": "login_expired", "message": str(e)}, 1)
    except ShopSwitchError as e:
        _emit({"status": "error", "errorType": "selector_error", "message": str(e)}, 1)
    except TekmetricError as e:
        _emit({"status": "error", "errorType": "other", "message": str(e)}, 1)
    except Exception as e:  # noqa: BLE001 -- last resort, must still emit valid JSON
        _emit({"status": "error", "errorType": "other", "message": f"Unexpected: {e}"}, 1)


if __name__ == "__main__":
    main()
