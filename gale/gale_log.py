"""
Logs Gale's activity to the standalone "Gale Invoicing Tracker" Google Sheet
(separate from the RO Tracker), which has two tabs:
  - "Gale Log": every RO Gale touches, whatever the outcome -- sent,
    drafted, skipped, or errored.
  - "ROs A/R":  only ROs that were actually sent and posted to A/R, using
    the same columns as Gale Log plus manual payment-tracking columns
    (Received, Processed, Payment Sent, Payment Received, Paid, Complete)
    that Reuben checks off by hand as a payment moves through DriveTime's
    process.
Logging failures are swallowed (logged, not raised) -- a Sheets hiccup
here should never stop an invoice from being sent or a run from
completing.
"""
import logging
from datetime import datetime

import gspread
from google.oauth2.service_account import Credentials

import config

log = logging.getLogger("daily_sweep")

GALE_LOG_TAB_NAME = "Gale Log"
AR_TAB_NAME = "ROs A/R"
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

_sh = None  # cached spreadsheet handle for this process
_ws_cache = {}  # tab name -> cached worksheet handle


def _spreadsheet():
    global _sh
    if _sh is not None:
        return _sh
    creds = Credentials.from_service_account_file(
        config.GOOGLE_SERVICE_ACCOUNT_PATH, scopes=SCOPES
    )
    gc = gspread.authorize(creds)
    _sh = gc.open_by_key(config.GALE_RO_LOG_SHEET_ID)
    return _sh


def _worksheet(tab_name):
    if tab_name in _ws_cache:
        return _ws_cache[tab_name]
    ws = _spreadsheet().worksheet(tab_name)
    _ws_cache[tab_name] = ws
    return ws


def _base_row(ro: dict, status: str, notes: str):
    return [
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ro.get("ro_number", ""),
        ro.get("shop_name", ""),
        ro.get("customer_name", ""),
        f"{ro.get('year', '')} {ro.get('model', '')}".strip(),
        ro.get("vin", ""),
        ro.get("claim_number", ""),
        ro.get("total_invoice", ""),
        status,
        notes,
    ]


def log_ro(ro: dict, status: str, notes: str = ""):
    """Appends one row to the Gale Log tab. ro is expected to have at
    least ro_number/customer_name; other fields are filled in when
    present, blank otherwise."""
    row = _base_row(ro, status, notes)
    try:
        _worksheet(GALE_LOG_TAB_NAME).append_row(row, value_input_option="USER_ENTERED")
    except Exception as e:  # noqa: BLE001 — logging must never break the sweep
        log.warning(f"Gale Log: failed to log RO #{ro.get('ro_number', '?')}: {e}")


def log_ar(ro: dict, status: str = "Sent & Posted to A/R", notes: str = ""):
    """Appends one row to the ROs A/R tab. Call this only for ROs that
    were actually sent and posted to A/R -- the 6 manual tracking columns
    (Received, Processed, Payment Sent, Payment Received, Paid, Complete)
    are left blank for Reuben to check off by hand."""
    row = _base_row(ro, status, notes) + ["", "", "", "", "", ""]
    try:
        _worksheet(AR_TAB_NAME).append_row(row, value_input_option="USER_ENTERED")
    except Exception as e:  # noqa: BLE001 — logging must never break the sweep
        log.warning(f"ROs A/R: failed to log RO #{ro.get('ro_number', '?')}: {e}")
