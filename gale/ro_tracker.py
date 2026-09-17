"""
Reads the RO Tracker Google Sheet and returns the list of DriveTime ROs
that are ready to invoice (RO Status=Complete, RO Label=Balance Due,
Customer Name contains "drivetime"). DriveTime Recon (DriveTime Inspection
Center Houston) is included in this list too -- daily_sweep.py routes its
invoices to a different recipient list, it isn't skipped.
"""

import time

import gspread
from google.oauth2.service_account import Credentials

import config


def _client():
    creds = Credentials.from_service_account_file(
        config.GOOGLE_SERVICE_ACCOUNT_PATH, scopes=config.SHEETS_SCOPES
    )
    return gspread.authorize(creds)


def _open_worksheet(retries=4, base_delay=5):
    """Google Sheets occasionally returns a transient 503 ('service
    currently unavailable'). Retry a few times with backoff before giving
    up — this runs unattended now, so nobody is there to notice and
    retry by hand."""
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            gc = _client()
            sh = gc.open_by_key(config.RO_TRACKER_SHEET_ID)
            if config.RO_TRACKER_WORKSHEET_NAME:
                return sh.worksheet(config.RO_TRACKER_WORKSHEET_NAME)
            return sh.sheet1
        except gspread.exceptions.APIError as e:
            last_err = e
            if attempt < retries:
                delay = base_delay * attempt
                time.sleep(delay)
            continue
    raise last_err


def _is_drivetime_in_scope(customer_name: str) -> bool:
    if not customer_name:
        return False
    name = customer_name.strip().lower()
    return any(term in name for term in config.CUSTOMER_NAME_MUST_CONTAIN_ANY)


def get_invoiceable_drivetime_ros():
    """
    Returns a list of dicts, one per RO ready to invoice, e.g.:
        {
            "shop_name": "MMP Miami",
            "customer_name": "Drivetime Miami",
            "ro_number": "1630",
            "year": "2017",
            "model": "Hyundai Tucson",
            "vin": "KM8J3CA4XHU545573",
            "claim_number": "C102630362",
            "total_invoice": "120",
            "row_index": 7,   # 1-based row number in the sheet, for reference
        }
    """
    ws = _open_worksheet()
    records = ws.get_all_records()  # list of dicts keyed by header row

    results = []
    for i, row in enumerate(records, start=2):  # row 1 is the header
        status = str(row.get(config.COL_RO_STATUS, "")).strip()
        label = str(row.get(config.COL_RO_LABEL, "")).strip()
        customer_name = str(row.get(config.COL_CUSTOMER_NAME, "")).strip()

        if status != config.READY_RO_STATUS:
            continue
        if label != config.READY_RO_LABEL:
            continue
        if not _is_drivetime_in_scope(customer_name):
            continue

        results.append({
            "shop_name": str(row.get(config.COL_SHOP_NAME, "")).strip(),
            "customer_name": customer_name,
            "ro_number": str(row.get(config.COL_RO_NUMBER, "")).strip(),
            "year": str(row.get(config.COL_YEAR, "")).strip(),
            "model": str(row.get(config.COL_MODEL, "")).strip(),
            "vin": str(row.get(config.COL_VIN, "")).strip(),
            "claim_number": str(row.get(config.COL_CLAIM_NUMBER, "")).strip(),
            "total_invoice": str(row.get(config.COL_TOTAL_INVOICE, "")).strip(),
            "row_index": i,
        })

    return results


def ro_key(ro: dict) -> str:
    """Stable unique key for an RO, used for idempotency in state.py."""
    return f"{ro['shop_name']}|{ro['ro_number']}"

