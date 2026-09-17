"""
ONE-TIME SETUP — run this yourself, once, on a machine with a web browser.

Before running this:
  1. Go to https://console.cloud.google.com/ (you can reuse the existing
     "Benny Agent" project, or make a new one called "Gale Agent").
  2. Enable the Gmail API for that project.
  3. Create an OAuth Client ID (type: Desktop app).
  4. Download the client secret JSON and save it as:
       credentials/gmail_client_secret.json

Then run:
    python3 gmail_auth_setup.py

A browser tab will open asking you to log in as reuben@mobilemechanicpartners.com
and approve access. After that, credentials/gmail_token.json is saved and
Gale can send/draft emails as you without asking again (it auto-refreshes).
"""

import os
from google_auth_oauthlib.flow import InstalledAppFlow

import config


def main():
    os.makedirs(config.CREDENTIALS_DIR, exist_ok=True)
    if not os.path.exists(config.GMAIL_CLIENT_SECRET_PATH):
        raise SystemExit(
            f"Missing {config.GMAIL_CLIENT_SECRET_PATH}\n"
            "Download it from Google Cloud Console first (see this file's docstring)."
        )
    flow = InstalledAppFlow.from_client_secrets_file(
        config.GMAIL_CLIENT_SECRET_PATH, config.GMAIL_SCOPES
    )
    creds = flow.run_local_server(port=0)
    with open(config.GMAIL_TOKEN_PATH, "w") as f:
        f.write(creds.to_json())
    print(f"Saved Gmail token to {config.GMAIL_TOKEN_PATH}")


if __name__ == "__main__":
    main()
