"""
Gale — DriveTime Invoicing Agent
Central configuration. Edit this file to adjust behavior without touching
the rest of the code.
"""

import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CREDENTIALS_DIR = os.path.join(BASE_DIR, "credentials")
STATE_DIR = os.path.join(BASE_DIR, "state")
LOGS_DIR = os.path.join(BASE_DIR, "logs")
DOWNLOADS_DIR = os.path.join(BASE_DIR, "downloads")  # invoice PDFs land here

# ---------------------------------------------------------------------------
# RO Tracker (Google Sheet) — source of truth for "which ROs are ready"
# ---------------------------------------------------------------------------
RO_TRACKER_SHEET_ID = "1BUl2mRn5ly-7dOfe2Wjm-H_0Wvu-ijPQQtPE5tihc2o"
RO_TRACKER_WORKSHEET_NAME = None  # None = first/default worksheet

# ---------------------------------------------------------------------------
# Gale Invoicing Tracker (separate Google Sheet, not a tab on the RO Tracker) -- has
# two tabs: "Gale Log" (every RO Gale touches) and "ROs A/R" (only ROs
# sent + posted to A/R, with extra manual payment-tracking columns).
# ---------------------------------------------------------------------------
GALE_RO_LOG_SHEET_ID = "1flcfquOMEcWmefRAKprdRpW4leQ8Z73SU7wpebooxQw"

# Column headers exactly as they appear in row 1 of the RO Tracker.
COL_SHOP_NAME = "Shop Name"
COL_CUSTOMER_NAME = "Customer Name"
COL_RO_NUMBER = "Repair Order Number"
COL_YEAR = "Year"
COL_MODEL = "Model"
COL_VIN = "Vehicle Identification Number (VIN)"
COL_CLAIM_NUMBER = "KeyTag (Claim #)"
COL_RO_STATUS = "RO Status"     # column K
COL_RO_LABEL = "RO Label"       # column L
COL_TOTAL_INVOICE = "Total Invoice"

# Values that mean "done, unbilled, ready for Gale to invoice"
READY_RO_STATUS = "Complete"
READY_RO_LABEL = "Balance Due"

# Values Tekmetric shows once an RO has been posted to A/R (used to verify
# the RO Tracker synced back after Gale posts in Tekmetric).
POSTED_RO_STATUS = "Accounts Receivable"
POSTED_RO_LABEL = "In A/R"

# ---------------------------------------------------------------------------
# DriveTime scope for V1
# ---------------------------------------------------------------------------
# Customers Gale is cleared to invoice on its own, matched as a
# case-insensitive substring of the RO Tracker's Customer Name. Carvana bills
# through the same inbox as DriveTime and was already being invoiced that way
# by the n8n workflow; listing it here lets the one sweep cover everything Gale
# used to do across two systems. Every other customer on the tracker is
# invoiced by hand and is deliberately out of scope.
CUSTOMER_NAME_MUST_CONTAIN_ANY = ("drivetime", "carvana")
CUSTOMER_NAME_MUST_CONTAIN = "drivetime"  # still read by the confirmation tracker

# DriveTime Inspection Center Houston ("Recon") — same Complete/Balance Due
# scope as every other DriveTime RO, but its invoices route to a different
# set of recipients instead of the standard DRIVETIME_INVOICE_EMAIL below.
CUSTOMER_NAME_RECON_EXACT = {
    "drivetime inspection center houston",
}

DRIVETIME_INVOICE_EMAIL = "invoices@drivetime.com"
RECON_INVOICE_EMAILS = [
    "DL-TexasKuykendahlICManagers@drivetime.com",
    "Devonta.curvey@drivetime.com",
    "kristin.dellafosse@drivetime.com",
]

# ---------------------------------------------------------------------------
# Tekmetric
# ---------------------------------------------------------------------------
TEKMETRIC_URL = "https://shop.tekmetric.com"
# Shops Gale should sweep. Add/remove as MMP opens or closes markets.
TEKMETRIC_SHOPS = ["MMP Dallas", "MMP Houston", "MMP Miami", "MMP Tampa"]
# Persistent browser profile so Gale doesn't have to log in (and solve a
# reCAPTCHA) every run. Created once via scripts/tekmetric_login_setup.py.
TEKMETRIC_STORAGE_STATE = os.path.join(CREDENTIALS_DIR, "tekmetric_storage_state.json")

# ---------------------------------------------------------------------------
# Gmail (send-as reuben@mobilemechanicpartners.com)
# ---------------------------------------------------------------------------
GMAIL_SENDER = "reuben@mobilemechanicpartners.com"
GMAIL_TOKEN_PATH = os.path.join(CREDENTIALS_DIR, "gmail_token.json")
GMAIL_CLIENT_SECRET_PATH = os.path.join(CREDENTIALS_DIR, "gmail_client_secret.json")
GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
]

# Google Sheets read access (RO Tracker)
SHEETS_SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
GOOGLE_SERVICE_ACCOUNT_PATH = os.path.join(CREDENTIALS_DIR, "google_service_account.json")

# ---------------------------------------------------------------------------
# Approval model (V1 = draft-and-wait; flip to True once Reuben trusts Gale)
# ---------------------------------------------------------------------------
AUTO_SEND = True

# ---------------------------------------------------------------------------
# State / idempotency
# ---------------------------------------------------------------------------
STATE_FILE = os.path.join(STATE_DIR, "gale_state.json")
PENDING_BATCH_FILE = os.path.join(STATE_DIR, "pending_batch.json")
SKIPPED_LOG_FILE = os.path.join(STATE_DIR, "skipped_log.json")
