"""
Extractor module - handles browser automation to extract direct download URLs.
Uses nodriver (undetected Chrome via CDP) to interact with fuckingfast.co pages
and retrieve the real file link, bypassing Cloudflare Turnstile/challenge.

Flow:
  1. Launch real Chrome via nodriver (undetectable by Cloudflare)
  2. Navigate to the page — Cloudflare challenge auto-resolves
  3. Clear window.dynamic to skip the ad-opening first click
  4. Set up a CDP network listener for the POST /f/<id>/go response
  5. Click the DOWNLOAD button — HTMX fires the POST directly
  6. Capture the HX-Redirect header from the response — that's the download URL
"""

import asyncio
from urllib.parse import urlparse

import nodriver as uc
from nodriver.cdp import network

from logger.log_utils import log


# ---------------------------------------------------------------------------
# Module-level browser + tab (reused across files for cookie persistence)
# ---------------------------------------------------------------------------
_browser = None
_last_tab = None   # tracks current tab so it can be cleaned up on errors


async def _get_browser():
    """Return a shared browser instance, launching one if needed.

    Also verifies the existing connection is still alive — Chrome sessions
    can go stale when the browser sits idle during long downloads.
    """
    global _browser
    if _browser is not None:
        # Health-check: verify the browser process is still alive.
        # Chrome can exit unexpectedly while idle during long downloads.
        is_dead = False
        try:
            # Check if the underlying Chrome process has exited
            proc = getattr(_browser, '_process', None) or getattr(_browser, 'process', None)
            if proc and proc.returncode is not None:
                is_dead = True
        except Exception:
            is_dead = True

        if is_dead:
            log("  [BROWSER] Browser process died — restarting...")
            _browser = None
            await asyncio.sleep(1)

    if _browser is None:
        _browser = await uc.start(
            headless=False,
            browser_args=[
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-popup-blocking",
                # Move window completely off-screen so it's invisible,
                # but still runs headed to avoid Cloudflare headless detection
                "--window-position=-32000,-32000",
                "--window-size=1,1",
            ],
        )
    return _browser


async def _shutdown_browser():
    """Shut down the shared browser instance (and any lingering tab)."""
    global _browser, _last_tab
    _last_tab = None
    if _browser is not None:
        try:
            _browser.stop()
        except Exception:
            pass
        _browser = None


# Errors that indicate the browser/websocket connection died
_CONNECTION_ERRORS = (
    "close frame",          # "no close frame received or sent"
    "not found",            # "Session with given id not found"
    "connection closed",    # websocket closed unexpectedly
    "websocket",            # generic websocket errors
    "event loop",           # event loop issues
)


def _is_connection_dead(error: Exception) -> bool:
    """Check if an error indicates the browser connection is stale/dead."""
    msg = str(error).lower()
    return any(pattern in msg for pattern in _CONNECTION_ERRORS)


async def _restart_browser():
    """Kill the stale browser and launch a fresh one."""
    global _browser, _last_tab
    log("  [BROWSER] Connection lost — restarting Chrome...")
    _last_tab = None
    if _browser is not None:
        try:
            _browser.stop()
        except Exception:
            pass
        _browser = None
    # Small delay to let Chrome fully exit before relaunching
    await asyncio.sleep(2)
    return await _get_browser()


async def extract_download_urls(links: list[str]) -> list[dict]:
    """
    Launch a real Chrome browser via nodriver, visit each fuckingfast link,
    and extract the real download URL by simulating the click flow.

    Returns a list of dicts:
        [{ "original": <url>, "download_url": <url or None>, "status": "ok" | "failed" }]
    """
    results = []
    browser = await _get_browser()

    for link in links:
        try:
            result = await _extract_from_tab(browser, link)
            results.append(result)
        except Exception as e:
            # If the browser connection died, restart and let main.py retry
            if _is_connection_dead(e):
                browser = await _restart_browser()
            log(f"  [EXTRACT ERROR] {e}")
            results.append({
                "original": link,
                "download_url": None,
                "status": "failed",
            })

    return results


async def _extract_from_tab(browser, link: str) -> dict:
    """
    Extract the download URL from a single fuckingfast.co page.

    The site uses a two-click mechanism:
      - 1st click: opens an ad URL (via window.dynamic), blocks the HTMX POST
      - 2nd click: window.dynamic is empty, so HTMX fires POST /f/<id>/go
        which returns HX-Redirect header with the real download URL

    We bypass the first click by clearing window.dynamic before clicking,
    so the very first click triggers the HTMX POST directly.

    The tab is closed after extraction. Cloudflare clearance cookies persist
    at the browser level, so closing the tab doesn't affect CF status.
    """
    global _last_tab

    file_id = _extract_file_id(link)
    if not file_id:
        raise ValueError(f"Could not extract file ID from URL: {link}")

    # Navigate to the new page (opens in a new tab)
    tab = await browser.get(link)

    # Now that the new tab is open, close the previous one
    if _last_tab is not None:
        try:
            await _last_tab.close()
        except Exception:
            pass
        _last_tab = None

    try:
        # Wait for Cloudflare challenge to resolve.
        # nodriver uses real Chrome so the challenge auto-solves,
        # but it may take 10-30s on first load. Subsequent pages reuse cookies.
        download_btn = None
        for attempt in range(60):  # up to 60s
            await asyncio.sleep(1)

            # Check if we're past Cloudflare by looking for the download button
            try:
                download_btn = await tab.select("a.gay-button, a[hx-post]", timeout=1)
                if download_btn:
                    break
            except Exception:
                pass

            # Log progress every 10 seconds
            if attempt > 0 and attempt % 10 == 0:
                title = await tab.evaluate("document.title")
                if "moment" in title.lower() or "challenge" in title.lower():
                    log(f"  [CF] Still resolving Cloudflare challenge... ({attempt}s)")
                else:
                    log(f"  [WAIT] Page loading... ({attempt}s)")

        if not download_btn:
            # Last resort check
            title = await tab.evaluate("document.title")
            if "moment" in title.lower() or "challenge" in title.lower():
                raise ConnectionError("Stuck on Cloudflare challenge page after 60s")
            raise ValueError("Download button not found on page after 60s")

        # Clear window.dynamic to skip the ad-opening first click
        await tab.evaluate("window.dynamic = ''")

        # Set up CDP network interception to capture the POST response
        download_url = None
        response_captured = asyncio.Event()

        async def handle_response(event: network.ResponseReceived):
            nonlocal download_url
            resp = event.response

            if f"/f/{file_id}/go" in resp.url:
                headers = resp.headers
                # Check for HX-Redirect header (HTMX convention)
                hx_redirect = headers.get("hx-redirect") or headers.get("HX-Redirect")
                if hx_redirect:
                    download_url = hx_redirect
                    response_captured.set()
                    return

                # Check for standard Location header (302 redirect)
                location = headers.get("location") or headers.get("Location")
                if location:
                    download_url = location
                    response_captured.set()
                    return

        # Enable network tracking and add handler
        await tab.send(network.enable())
        tab.add_handler(network.ResponseReceived, handle_response)

        # Click the DOWNLOAD button
        await download_btn.click()

        # Wait for the response to be captured (with timeout)
        try:
            await asyncio.wait_for(response_captured.wait(), timeout=15.0)
        except asyncio.TimeoutError:
            pass

        if not download_url:
            # Second attempt: clear window.dynamic again and retry click
            await tab.evaluate("window.dynamic = ''")

            # Re-select the button in case the DOM changed
            try:
                download_btn = await tab.select("a.gay-button, a[hx-post]", timeout=3)
                if download_btn:
                    await download_btn.click()
            except Exception:
                pass

            try:
                await asyncio.wait_for(response_captured.wait(), timeout=10.0)
            except asyncio.TimeoutError:
                pass

        if not download_url:
            raise ValueError("Could not capture download URL from POST response")

        return {
            "original": link,
            "download_url": download_url,
            "status": "ok",
        }

    finally:
        # Always close the tab — Cloudflare clearance cookies are stored at the
        # browser level, so closing the tab won't affect CF status for future pages.
        # Keeping tabs alive causes stale CDP sessions during long downloads.
        try:
            await tab.close()
        except Exception:
            pass
        _last_tab = None


def _extract_file_id(url: str) -> str | None:
    """
    Extract the file ID from a fuckingfast.co URL.
    e.g. 'https://fuckingfast.co/fpr2096aojov#...' → 'fpr2096aojov'
    """
    parsed = urlparse(url)
    path = parsed.path.strip("/")
    if path:
        return path.split("/")[0]
    return None


async def extract_single_url(link: str) -> dict:
    """
    Extract the download token for a SINGLE link.
    Reuses extract_download_urls() with a single-element list.

    Returns:
        dict: { "original": url, "download_url": url|None, "status": "ok"|"failed" }
    """
    results = await extract_download_urls([link])
    return results[0] if results else {
        "original": link,
        "download_url": None,
        "status": "failed",
    }
