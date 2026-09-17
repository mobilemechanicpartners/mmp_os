"""
Reads DriveTime's own confirmation emails out of Gmail and uses them to fill
in the "Processed" and "Paid" columns on the "ROs A/R" tab of the Gale Invoicing Tracker
spreadsheet -- so the sheet shows, for every RO Gale has invoiced: sent ->
DriveTime processed it -> DriveTime paid it.

Two email types are read:

  1. "Your invoice was processed." from RM-RepairInvoices@drivetime.com.
     Body has a line like "Invoice #: 1642" -- that's the RO number.
     -> fills the "Processed" column with the date of that email.
     (Recon ROs, sent to the special DriveTime Inspection Center Houston
     addresses, do not get one of these -- that's expected, not an error.)

  2. Payment emails from Tipalti@drivetime.com ("... submitted a payment to
     you"). Two layouts show up:
       a) a table of "USD <amount> | Invoice | <RO number>" rows (used for
          both regular and Recon DriveTime payments)
       b) a single line "Payment message: Invoice #<RO number>, dated ..."
     -> fills the "Paid" column with the date of that email (and the amount,
        for reference).

Both columns are only ever filled in when they're still blank -- this never
overwrites something Reuben (or an earlier run) already put there. Filled
cells get a light green background so they're easy to spot at a glance.

Safe to re-run any time (e.g. daily, right after the invoice sweep) -- it
just fills in whatever new confirmations have come in since the last run.
"""
import base64
import os
import re
import sys
from datetime import datetime

import gspread
from google.oauth2.service_account import Credentials

import config
import gmail_client

SHEETS_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]
AR_TAB_NAME = "ROs A/R"
GREEN = {"red": 0.79, "green": 0.93, "blue": 0.79}

PROCESSED_QUERY = 'from:RM-RepairInvoices@drivetime.com subject:"invoice was processed" newer_than:120d'
PAYMENT_QUERY = "from:Tipalti@drivetime.com newer_than:120d"


def _sheet():
    creds = Credentials.from_service_account_file(config.GOOGLE_SERVICE_ACCOUNT_PATH, scopes=SHEETS_SCOPES)
    gc = gspread.authorize(creds)
    return gc.open_by_key(config.GALE_RO_LOG_SHEET_ID).worksheet(AR_TAB_NAME)


def _list_message_ids(service, query):
    ids = []
    page_token = None
    while True:
        resp = service.users().messages().list(
            userId="me", q=query, pageToken=page_token, maxResults=100
        ).execute()
        ids.extend(m["id"] for m in resp.get("messages", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return ids


def _fetch_messages(service, message_ids, chunk_size=50):
    """Fetches many messages using Gmail's batch endpoint (a handful of
    HTTP round trips instead of one per message -- one-per-message is what
    made this take ~20 minutes on a mailbox with a long history)."""
    messages = {}

    def _capture(request_id, response, exception):
        if exception is None:
            messages[request_id] = response

    for i in range(0, len(message_ids), chunk_size):
        chunk = message_ids[i:i + chunk_size]
        batch = service.new_batch_http_request(callback=_capture)
        for mid in chunk:
            batch.add(
                service.users().messages().get(userId="me", id=mid, format="full"),
                request_id=mid,
            )
        batch.execute()
    return messages


def _plain_text(payload):
    """Walks a Gmail message payload and returns the text/plain body."""
    if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", "replace")
    for part in payload.get("parts", []) or []:
        text = _plain_text(part)
        if text:
            return text
    return ""


def _date_str(internal_date_ms):
    return datetime.fromtimestamp(int(internal_date_ms) / 1000).strftime("%-m/%-d/%Y")


def _clean_ro(raw):
    """'20737R' -> '20737', '1,641' -> '1641' -- normalize to digits only so
    spreadsheet number formatting (commas, stray spaces) can't break a match."""
    return re.sub(r"\D", "", raw.strip())


def fetch_processed(service):
    """Returns {ro_number: date_str} from 'invoice was processed' emails."""
    out = {}
    ids = _list_message_ids(service, PROCESSED_QUERY)
    for mid, msg in _fetch_messages(service, ids).items():
        body = _plain_text(msg["payload"])
        m = re.search(r"Invoice #:\s*(\S+)", body)
        if not m:
            continue
        ro = _clean_ro(m.group(1))
        date_str = _date_str(msg["internalDate"])
        # keep the earliest processed date if an RO shows up more than once
        if ro not in out:
            out[ro] = date_str
    return out


def fetch_payments(service):
    """Returns {ro_number: (date_str, amount_str)} from Tipalti payment emails."""
    out = {}
    ids = _list_message_ids(service, PAYMENT_QUERY)
    for mid, msg in _fetch_messages(service, ids).items():
        body = _plain_text(msg["payload"])
        date_str = _date_str(msg["internalDate"])

        found = []
        # Layout (a): table rows "| USD 195.00 | Invoice | 20736 |"
        for amt, ro in re.findall(r"USD\s*([\d,.]+)\s*\|\s*Invoice\s*\|\s*([A-Za-z0-9]+)", body):
            found.append((_clean_ro(ro), amt))
        # Layout (b): "Payment message: Invoice #1623, dated ..." + "for 192 and covers"
        if not found:
            ro_m = re.search(r"Invoice #(\w+)", body)
            amt_m = re.search(r"for\s+([\d,.]+)\s+and covers", body)
            if ro_m:
                found.append((_clean_ro(ro_m.group(1)), amt_m.group(1) if amt_m else ""))

        for ro, amt in found:
            if ro not in out:
                out[ro] = (date_str, amt)
    return out


def main():
    service = gmail_client._service()
    print("Fetching 'processed' confirmations from Gmail...")
    processed = fetch_processed(service)
    print(f"  found {len(processed)} ROs with a processed confirmation")

    print("Fetching Tipalti payment emails from Gmail...")
    payments = fetch_payments(service)
    print(f"  found {len(payments)} ROs with a payment confirmation")

    ws = _sheet()
    header = ws.row_values(1)
    col = {name: i + 1 for i, name in enumerate(header)}
    ro_col = col["RO Number"]
    processed_col = col["Processed"]
    paid_col = col["Paid"]

    all_rows = ws.get_all_values()
    updates = []
    formats = []
    filled_processed = 0
    filled_paid = 0

    for row_idx, row in enumerate(all_rows[1:], start=2):  # skip header
        ro_raw = (row[ro_col - 1] if len(row) >= ro_col else "").strip()
        if not ro_raw:
            continue
        ro = _clean_ro(ro_raw)

        existing_processed = row[processed_col - 1] if len(row) >= processed_col else ""
        if not existing_processed and ro in processed:
            a1 = gspread.utils.rowcol_to_a1(row_idx, processed_col)
            updates.append({"range": a1, "values": [[processed[ro]]]})
            formats.append(a1)
            filled_processed += 1

        existing_paid = row[paid_col - 1] if len(row) >= paid_col else ""
        if not existing_paid and ro in payments:
            date_str, amt = payments[ro]
            value = f"{date_str} (${amt})" if amt else date_str
            a1 = gspread.utils.rowcol_to_a1(row_idx, paid_col)
            updates.append({"range": a1, "values": [[value]]})
            formats.append(a1)
            filled_paid += 1

    if updates:
        ws.batch_update(updates, value_input_option="USER_ENTERED")
        for a1 in formats:
            ws.format(a1, {"backgroundColor": GREEN})

    print(f"Updated {filled_processed} 'Processed' cells and {filled_paid} 'Paid' cells.")


if __name__ == "__main__":
    main()
