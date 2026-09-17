"""
ONE-TIME SETUP — run this yourself, once, on your Mac (not headless).

It opens a real, visible Chrome window at Tekmetric. Log in exactly as you
normally do (email/password + reCAPTCHA). Once you can see the shop
dashboard, come back to the terminal and press Enter — this saves your
logged-in session to credentials/tekmetric_storage_state.json so Gale can
reuse it without ever touching your password or the reCAPTCHA herself.

Re-run this whenever Tekmetric logs you out (sessions typically last weeks).

Usage:
    python3 tekmetric_login_setup.py
"""

import os
from playwright.sync_api import sync_playwright

import config


def main():
    os.makedirs(config.CREDENTIALS_DIR, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto(config.TEKMETRIC_URL)

        print("\nA Chrome window just opened.")
        print("1. Log in to Tekmetric as you normally would.")
        print("2. Once you see your shop dashboard (the list of MMP shops), come back here.")
        input("Press Enter once you're logged in and can see the shop dashboard... ")

        context.storage_state(path=config.TEKMETRIC_STORAGE_STATE)
        print(f"\nSaved. Gale can now log into Tekmetric on her own using this session.")
        print(f"File: {config.TEKMETRIC_STORAGE_STATE}")
        browser.close()


if __name__ == "__main__":
    main()
