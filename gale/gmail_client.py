"""
Gmail API helper — sends as reuben@mobilemechanicpartners.com.

Two modes used by Gale:
  - create_draft(...)   -> makes a Gmail draft Reuben can review/edit/send
                            himself from his inbox (used for the invoice
                            emails while AUTO_SEND is False)
  - send_message(...)   -> sends immediately (used once AUTO_SEND is True,
                            and always used for the session summary report)
"""

import base64
import mimetypes
import os
import time
from email.message import EmailMessage

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

import config


def _get_credentials():
    creds = None
    if os.path.exists(config.GMAIL_TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(config.GMAIL_TOKEN_PATH, config.GMAIL_SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            raise RuntimeError(
                "No valid Gmail token found. Run gmail_auth_setup.py once "
                "(on a machine with a browser) before running Gale."
            )
        with open(config.GMAIL_TOKEN_PATH, "w") as f:
            f.write(creds.to_json())
    return creds


def _service():
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
            time.sleep(delay)


def _build_mime_message(to, subject, body_text, attachment_path=None, body_html=None):
    message = EmailMessage()
    message["To"] = to
    message["From"] = config.GMAIL_SENDER
    message["Subject"] = subject
    message.set_content(body_text)
    if body_html:
        message.add_alternative(body_html, subtype="html")

    if attachment_path:
        ctype, encoding = mimetypes.guess_type(attachment_path)
        if ctype is None:
            ctype = "application/octet-stream"
        maintype, subtype = ctype.split("/", 1)
        with open(attachment_path, "rb") as f:
            message.add_attachment(
                f.read(),
                maintype=maintype,
                subtype=subtype,
                filename=os.path.basename(attachment_path),
            )

    encoded = base64.urlsafe_b64encode(message.as_bytes()).decode()
    return {"raw": encoded}


def create_draft(to, subject, body_text, attachment_path=None):
    service = _service()
    raw_message = _build_mime_message(to, subject, body_text, attachment_path)
    draft = _execute(service.users().drafts().create(userId="me", body={"message": raw_message}))
    return draft["id"]


def send_draft(draft_id):
    service = _service()
    sent = _execute(service.users().drafts().send(userId="me", body={"id": draft_id}))
    return sent["id"]  # this is the sent message's id


def send_message(to, subject, body_text, attachment_path=None, body_html=None):
    service = _service()
    raw_message = _build_mime_message(to, subject, body_text, attachment_path, body_html)
    sent = _execute(service.users().messages().send(userId="me", body=raw_message))
    return sent["id"]


def verify_sent(message_id) -> bool:
    """Confirms a message id actually shows up under SENT."""
    service = _service()
    msg = _execute(service.users().messages().get(userId="me", id=message_id, format="minimal"))
    return "SENT" in msg.get("labelIds", [])
