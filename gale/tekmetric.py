"""
Playwright automation for Tekmetric — the exact click-path validated live
on 2026-08-29 against RO #1630 (MMP Miami), hardened on 2026-09-10 after a
week of intermittent "shop-switcher" selector timeouts (see select_shop):

 1. Job Board -> search for the RO -> open it
 2. Payment tab -> "View & Share Invoice" -> grab the Print link's URL ->
    render that page straight to a PDF (no browser print-dialog clicking,
    since that's OS/browser chrome Playwright can't reliably drive —
    page.pdf() renders the page itself instead)
 3. Payment tab -> "Send to A/R" -> confirm the "Post to Accounts
    Receivable" dialog -> verify the RO now reads "In A/R"

2026-09-10 hardening:
 - select_shop() now tries several selector strategies, and reloads once
   for a clean retry before giving up — a full week of logs showed the
   same RO succeeding on a later run with zero code changes, which is the
   signature of a timing/render flake rather than a permanent UI change.
 - Every failure in this module now captures a screenshot before raising,
   so a human (or Claude) can see exactly what the page looked like
   instead of guessing from a stack trace.
 - Login-expired and shop-switch failures are now distinct exception
   types, so callers (process_ro.py) can route alerts correctly instead
   of treating every failure the same way.
 - is_in_ar() lets a caller check whether an RO was already posted before
   touching it again — idempotency, so a retry can never double-process.
"""

import os
import re
import time
from datetime import datetime

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

import config


class TekmetricError(RuntimeError):
    pass


class LoginExpiredError(TekmetricError):
    """The saved Tekmetric session is no longer valid. Needs a human to
    re-run tekmetric_login_setup.py — this is not something code can fix."""
    pass


class ShopSwitchError(TekmetricError):
    """Could not switch to the requested shop after trying every known
    selector and one reload. Likely a Tekmetric UI change — needs a
    developer to look at the fresh screenshot and update the selectors."""
    pass


SCREENSHOTS_DIR = os.path.join(config.BASE_DIR, "screenshots")


def _screenshot(page, label: str) -> str:
    """Best-effort screenshot for post-mortems. Never raises — a failed
    screenshot should never mask the real error."""
    try:
        os.makedirs(SCREENSHOTS_DIR, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(SCREENSHOTS_DIR, f"{label}_{ts}.png")
        page.screenshot(path=path, full_page=True)
        return path
    except Exception:  # noqa: BLE001 -- screenshotting must never break the real flow
        return ""


def _settle(page, timeout=8000):
    """Tekmetric's SPA has constant background network activity, so
    networkidle never fires reliably — wait for it but don't fail the
    whole run if it times out; the page is almost always usable by then."""
    try:
        page.wait_for_load_state("networkidle", timeout=timeout)
    except PlaywrightTimeoutError:
        pass


def _goto(page, url, attempts=3):
    """Tekmetric's cold SPA loads and its login redirect regularly blow past
    a 30s "load" wait, which used to fail the whole RO. Waiting for
    domcontentloaded instead — and retrying twice — turns those into a
    non-event."""
    last_error = None
    for attempt in range(attempts):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            return
        except Exception as e:  # noqa: BLE001 -- retried below, re-raised at the end
            last_error = e
            page.wait_for_timeout(2000 * (attempt + 1))
    raise TekmetricError(f"Could not load {url} after {attempts} attempts: {last_error}")


def _looks_like_login_page(page) -> bool:
    """Heuristic for 'the saved session expired and Tekmetric bounced us
    back to a login screen' — distinct from a shop-switcher UI problem."""
    try:
        content = page.content().lower()
    except Exception:  # noqa: BLE001
        return False
    return ("log in" in content or "sign in" in content) and "password" in content


class TekmetricSession:
    def __init__(self, headless=True):
        self._playwright = None
        self._browser = None
        self.context = None
        self.page = None
        self.headless = headless

    def __enter__(self):
        if not os.path.exists(config.TEKMETRIC_STORAGE_STATE):
            raise LoginExpiredError(
                "No saved Tekmetric login found. Run tekmetric_login_setup.py "
                "once (on a machine with a display) before running Gale."
            )
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=self.headless)
        self.context = self._browser.new_context(
            storage_state=config.TEKMETRIC_STORAGE_STATE
        )
        self.page = self.context.new_page()
        _goto(self.page, config.TEKMETRIC_URL)
        _settle(self.page)
        if _looks_like_login_page(self.page):
            _screenshot(self.page, "login_expired")
            raise LoginExpiredError(
                "Tekmetric bounced us to a login screen — the saved session "
                "has expired. Needs a human to re-run tekmetric_login_setup.py."
            )
        self.save_session()
        return self

    def save_session(self):
        """Write the browser's cookies back to disk after a good load.

        Tekmetric issues a rolling session: it stays valid as long as it keeps
        being used, but the saved copy on disk was never updated, so it aged
        out every few days and a human had to log in by hand. Saving it back
        on every run is what makes the login survive indefinitely.

        Written via a temp file and an atomic replace — a crash mid-write
        would otherwise leave a truncated session file, which is worse than
        an expired one.
        """
        if not self.context:
            return
        try:
            tmp = config.TEKMETRIC_STORAGE_STATE + ".tmp"
            self.context.storage_state(path=tmp)
            os.replace(tmp, config.TEKMETRIC_STORAGE_STATE)
        except Exception:  # noqa: BLE001 -- session upkeep must never break a run
            pass

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.save_session()
        if self.context:
            self.context.close()
        if self._browser:
            self._browser.close()
        if self._playwright:
            self._playwright.stop()

    # ------------------------------------------------------------------
    def select_shop(self, shop_name: str):
        """Switch to shop_name. Tries, in order: (1) a shop tile on the
        dashboard, (2) the header shop-switcher dropdown, (3) the same two
        again after one full reload. Raises ShopSwitchError only after all
        of that fails, with a screenshot saved for debugging."""
        page = self.page

        def _attempt() -> bool:
            _goto(page, config.TEKMETRIC_URL)
            _settle(page)
            if _looks_like_login_page(page):
                _screenshot(page, "login_expired")
                raise LoginExpiredError(
                    "Tekmetric bounced us to a login screen mid-run — the "
                    "saved session has expired."
                )
            # Strategy 1: shop tile on the picker dashboard.
            try:
                page.get_by_text(shop_name, exact=True).first.click(timeout=5000)
                return True
            except PlaywrightTimeoutError:
                pass
            # Strategy 2: header shop-switcher dropdown — try a few known
            # selector shapes, since this is exactly what broke this week.
            switcher_selectors = [
                '[data-testid="shop-switcher"]',
                'header [aria-haspopup="listbox"]',
                'header button:has-text("MMP")',
                '[data-testid="shop-switcher"], header [aria-haspopup="listbox"]',
            ]
            for sel in switcher_selectors:
                try:
                    page.locator(sel).first.click(timeout=8000)
                    page.get_by_text(shop_name, exact=True).first.click(timeout=5000)
                    return True
                except PlaywrightTimeoutError:
                    continue
            return False

        if _attempt():
            _settle(page)
            return

        # One clean retry: full reload, short pause, try everything again.
        # A week of logs shows the exact same RO succeeding hours later
        # with zero code changes — that's a render-timing flake, and a
        # fresh page load is often enough to clear it.
        page.reload()
        page.wait_for_timeout(2000)
        _settle(page)
        if _attempt():
            _settle(page)
            return

        shot = _screenshot(page, f"shop_switch_failed_{shop_name.replace(' ', '_')}")
        raise ShopSwitchError(
            f"Could not switch to '{shop_name}' after trying every known "
            f"selector plus one reload. Screenshot saved to: {shot}"
        )

    # ------------------------------------------------------------------
    def open_repair_order(self, ro_number: str):
        page = self.page
        search_box = page.get_by_placeholder("Search Tekmetric...")
        search_box.click()
        search_box.fill(ro_number)
        page.keyboard.press("Enter")
        page.wait_for_timeout(1500)

        # Prefer an exact "RO#<number>" link if the search/job-board shows one.
        try:
            ro_link = page.get_by_text(f"RO#{ro_number}", exact=False).first
            ro_link.click(timeout=10000)
        except PlaywrightTimeoutError:
            _screenshot(page, f"ro_not_found_{ro_number}")
            raise TekmetricError(f"Could not find RO #{ro_number} in search results.")
        _settle(page)

        # Land on the Payment tab specifically (works from any starting tab).
        payment_tab = page.get_by_role("tab", name="Payment").or_(
            page.get_by_text("Payment", exact=True)
        )
        payment_tab.first.click()
        page.wait_for_timeout(1000)

    # ------------------------------------------------------------------
    def is_in_ar(self) -> bool:
        """Idempotency check: call after open_repair_order() to see if this
        RO has already been posted to A/R, so a retry never double-processes
        it. Cheap and safe to call every time before invoicing."""
        try:
            return "In A/R" in self.page.content()
        except Exception:  # noqa: BLE001 -- if we can't tell, assume not done
            return False

    # ------------------------------------------------------------------
    def download_invoice_pdf(self, ro_number: str, dest_dir: str = None) -> str:
        """
        Opens "View & Share Invoice" on the current RO's Payment tab, finds
        the Print link's direct URL, and renders that page to a PDF file.
        Returns the local file path.
        """
        page = self.page
        dest_dir = dest_dir or config.DOWNLOADS_DIR
        os.makedirs(dest_dir, exist_ok=True)

        page.get_by_text("View & Share Invoice", exact=True).click()
        page.wait_for_timeout(1000)

        print_link = page.get_by_role("link", name=re.compile("print", re.I)).first
        href = print_link.get_attribute("href")
        if not href:
            _screenshot(page, f"no_print_link_{ro_number}")
            raise TekmetricError("Could not find the invoice's Print link URL.")
        invoice_url = href if href.startswith("http") else config.TEKMETRIC_URL + href

        # Tekmetric serves this URL as a direct file download (not an HTML
        # page to screenshot/print) — Playwright surfaces that as a Download
        # event rather than a normal navigation, so we catch it directly.
        safe_name = re.sub(r'[\\/:*?"<>|]', "_", f"RO_{ro_number}_invoice.pdf")
        dest_path = os.path.join(dest_dir, safe_name)

        invoice_page = self.context.new_page()
        try:
            with invoice_page.expect_download(timeout=15000) as download_info:
                try:
                    invoice_page.goto(invoice_url)
                except Exception:
                    pass  # goto raises once the download starts — expected
            download = download_info.value
            download.save_as(dest_path)
        finally:
            invoice_page.close()

        # Back on the RO's invoice-preview modal — close it to return to Payment tab.
        page.keyboard.press("Escape")
        page.wait_for_timeout(500)
        return dest_path

    # ------------------------------------------------------------------
    def post_to_ar(self):
        """Clicks Send to A/R and confirms the Post dialog. Raises
        TekmetricError if the RO doesn't end up showing 'In A/R'."""
        page = self.page
        page.get_by_text("Send to A/R", exact=True).click()
        page.wait_for_timeout(800)

        post_button = page.get_by_role("button", name="Post", exact=True)
        post_button.click()
        page.wait_for_timeout(1500)

        if "In A/R" not in page.content():
            _screenshot(page, "post_to_ar_unconfirmed")
            raise TekmetricError(
                "Posted to A/R but the page did not confirm 'In A/R' — check manually."
            )

    # ------------------------------------------------------------------
    def get_shop_job_board_completed(self):
        """Returns the RO numbers currently shown in the Job Board's
        Completed column, for cross-checking against the RO Tracker."""
        page = self.page
        page.get_by_text("Job Board", exact=True).first.click()
        page.wait_for_timeout(1000)
        ro_texts = page.locator("text=/RO#\\d+/").all_inner_texts()
        return [t.replace("RO#", "").strip() for t in ro_texts]
