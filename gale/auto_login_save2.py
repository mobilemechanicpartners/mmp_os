"""
auto_login_save2.py — robust, hands-off Tekmetric session refresh.

Improvements over v1 (which failed to detect a successful login):
 - Uses a PERSISTENT Chrome profile on disk, so the login survives even if
   detection never fires or the process is killed. Nothing is lost.
 - Watches EVERY open tab, not just the first one (Tekmetric can open new
   tabs/windows after login, which v1 missed entirely).
 - Detects login by the presence of a real Tekmetric session cookie rather
   than by scraping page text, which was brittle and app-shell dependent.
 - Supports a force-save trigger file (.save_now) so the session can be
   captured on demand without depending on detection at all.

Marker file at ~/gale/credentials/.last_refresh reports status.
"""

import os
import time
import traceback

from playwright.sync_api import sync_playwright

import config

CRED_DIR = os.path.dirname(config.TEKMETRIC_STORAGE_STATE)
MARKER = os.path.join(CRED_DIR, ".last_refresh")
FORCE_SAVE = os.path.join(CRED_DIR, ".save_now")
PROFILE_DIR = os.path.join(CRED_DIR, "chrome_profile")
TIMEOUT_SECONDS = 1800  # 30 minutes


def _marker(text):
    try:
        with open(MARKER, "w") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {text}\n")
    except Exception:
        pass


def _has_session_cookie(context) -> bool:
    """A real logged-in Tekmetric session leaves auth cookies on the domain.
    Far more reliable than looking for text on the page."""
    try:
        for c in context.cookies():
            domain = (c.get("domain") or "").lower()
            name = (c.get("name") or "").lower()
            value = c.get("value") or ""
            if "tekmetric" in domain and len(value) > 20:
                # Ignore obvious non-auth cookies.
                if any(skip in name for skip in ("_ga", "_gid", "optimizely", "hubspot", "intercom")):
                    continue
                return True
    except Exception:
        pass
    return False


def _on_tekmetric_app(context) -> bool:
    """True when any open tab is on Tekmetric and not showing a password field."""
    for page in list(context.pages):
        try:
            url = (page.url or "").lower()
            if "tekmetric" not in url:
                continue
            if "login" in url or "signin" in url:
                continue
            if page.locator('input[type="password"]').count() > 0:
                continue
            return True
        except Exception:
            continue
    return False


def _save(context):
    context.storage_state(path=config.TEKMETRIC_STORAGE_STATE)
    _marker("SAVED OK")
    try:
        if os.path.exists(FORCE_SAVE):
            os.remove(FORCE_SAVE)
    except Exception:
        pass


def main():
    os.makedirs(PROFILE_DIR, exist_ok=True)
    try:
        if os.path.exists(FORCE_SAVE):
            os.remove(FORCE_SAVE)
    except Exception:
        pass

    _marker("STARTED v2 - waiting for login")

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            PROFILE_DIR,
            headless=False,
            no_viewport=True,
            args=["--start-maximized"],
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(config.TEKMETRIC_URL)

        deadline = time.time() + TIMEOUT_SECONDS
        stable = 0
        while time.time() < deadline:
            time.sleep(3)

            # Force save on demand, regardless of detection.
            if os.path.exists(FORCE_SAVE):
                try:
                    _save(context)
                except Exception:
                    _marker("FORCE SAVE FAILED " + traceback.format_exc()[-200:])
                continue

            try:
                if _has_session_cookie(context) and _on_tekmetric_app(context):
                    stable += 1
                    if stable >= 2:
                        _save(context)
                        # Keep running so the session can be re-saved later
                        # without forcing another login.
                        _marker("SAVED OK - browser still open, safe to close window")
                        stable = 0
                        time.sleep(20)
                else:
                    stable = 0
            except Exception:
                stable = 0

        _marker("TIMED OUT")
        try:
            context.close()
        except Exception:
            pass


if __name__ == "__main__":
    try:
        main()
    except Exception:
        _marker("ERROR " + traceback.format_exc()[-300:])
        raise
