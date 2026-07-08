"""
Extractor module - handles browser automation to extract direct download URLs.
Uses Playwright (async API) to interact with fuckingfast.co pages and retrieve the real file link.

Flow:
  1. Navigate to the page in a real browser (to pass Cloudflare)
  2. Clear window.dynamic to skip the ad-opening first click
  3. Set up a listener for the POST /f/<id>/go response
  4. Click the DOWNLOAD button — HTMX fires the POST directly
  5. Capture the HX-Redirect header from the response — that's the download URL
"""

import asyncio
from urllib.parse import urlparse
from playwright.async_api import async_playwright, Error as PlaywrightError


async def extract_download_urls(links: list[str]) -> list[dict]:
    """
    Launch a headless Chromium browser, visit each fuckingfast link,
    and extract the real download URL by simulating the two-click flow.

    Returns a list of dicts:
        [{ "original": <url>, "download_url": <url or None>, "status": "ok" | "failed" }]
    """
    results = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--no-default-browser-check",
                "--window-position=-32000,-32000",
            ],
        )

        # Use a persistent context with a realistic user-agent
        # This also keeps Cloudflare clearance cookies across pages
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 720},
        )

        for link in links:
            page = await context.new_page()
            try:
                result = await _extract_from_page(page, link)
                results.append(result)
            except Exception:
                results.append({
                    "original": link,
                    "download_url": None,
                    "status": "failed",
                })
            finally:
                await page.close()

        await context.close()
        await browser.close()

    return results


async def _extract_from_page(page, link: str) -> dict:
    """
    Extract the download URL from a single fuckingfast.co page.

    The site uses a two-click mechanism:
      - 1st click: opens an ad URL (via window.dynamic), blocks the HTMX POST
      - 2nd click: window.dynamic is empty, so HTMX fires POST /f/<id>/go
        which returns HX-Redirect header with the real download URL

    We bypass the first click by clearing window.dynamic before clicking,
    so the very first click triggers the HTMX POST directly.
    """
    file_id = _extract_file_id(link)
    if not file_id:
        raise ValueError(f"Could not extract file ID from URL: {link}")

    # Navigate to the page
    try:
        response = await page.goto(link, wait_until="domcontentloaded", timeout=30000)
    except PlaywrightError as nav_err:
        raise ConnectionError(f"Navigation error: {nav_err}") from nav_err

    if response and response.status == 404:
        raise ConnectionError("Page returned 404 Not Found")

    # Wait for Cloudflare challenge to resolve, then for the download button.
    # In headed mode, Cloudflare auto-solves but can take 10-20s on first load.
    # Subsequent pages in the same context reuse clearance cookies and are faster.
    try:
        await page.wait_for_selector(
            "a.gay-button, a[hx-post]",
            state="visible",
            timeout=30000,
        )
    except PlaywrightError:
        # Last resort: check if we're stuck on Cloudflare
        title = await page.title()
        if "moment" in title.lower() or "challenge" in title.lower():
            raise ConnectionError("Stuck on Cloudflare challenge page")
        raise ValueError("Download button not found on page")

    # Clear window.dynamic to skip the ad-opening first click
    # This makes the next click go straight to the HTMX POST
    await page.evaluate("window.dynamic = ''")

    # Set up a promise to capture the POST response BEFORE clicking
    # We listen for the response to /f/<id>/go
    download_url = None
    response_captured = asyncio.Event()

    async def handle_response(resp):
        nonlocal download_url
        if f"/f/{file_id}/go" in resp.url:
            # Check for HX-Redirect header (HTMX convention)
            headers = resp.headers
            hx_redirect = headers.get("hx-redirect")
            if hx_redirect:
                download_url = hx_redirect
                response_captured.set()
                return

            # Check for standard Location header (302 redirect)
            location = headers.get("location")
            if location:
                download_url = location
                response_captured.set()
                return

            # Fallback: check if server returned the URL in the response body
            try:
                body = await resp.text()
                body = body.strip()
                if body.startswith("http"):
                    download_url = body
                    response_captured.set()
            except Exception:
                pass

    page.on("response", handle_response)

    # Click the DOWNLOAD button
    download_btn = page.locator("a.gay-button, a[hx-post]").first
    await download_btn.click()

    # Also handle any popup that might open (close it immediately)
    page.on("popup", lambda popup: asyncio.ensure_future(popup.close()))

    # Wait for the response to be captured (with timeout)
    try:
        await asyncio.wait_for(response_captured.wait(), timeout=15.0)
    except asyncio.TimeoutError:
        pass

    if not download_url:
        # Second attempt: maybe window.dynamic wasn't cleared fast enough
        # or the click didn't trigger. Try clicking again.
        await page.evaluate("window.dynamic = ''")
        await download_btn.click()

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
