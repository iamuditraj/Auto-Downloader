"""
Extractor module - handles browser automation to extract direct download URLs.
Uses Playwright (async API) to interact with fuckingfast.co pages and retrieve the real file link.
"""

import asyncio
from playwright.async_api import async_playwright, Error as PlaywrightError


async def extract_download_urls(links: list[str]) -> list[dict]:
    """
    Launch a headless Chromium browser, visit each fuckingfast link,
    and extract the real (non-fuckingfast) download URL from the page.

    Returns a list of dicts:
        [{ "original": <url>, "download_url": <url or None>, "status": "ok" | "failed" }]
    """
    results = []
    total = len(links)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)

        for i, link in enumerate(links, start=1):
            print(f"[{i}/{total}] Extracting: {link}")

            page = await browser.new_page()
            try:
                # Navigate — catch 404s and network errors
                try:
                    response = await page.goto(link, wait_until="domcontentloaded", timeout=15000)
                except PlaywrightError as nav_err:
                    raise ConnectionError(f"Navigation error: {nav_err}") from nav_err

                if response and response.status == 404:
                    raise ConnectionError(f"Page returned 404 Not Found")

                # The download URL is hardcoded in the page's inline JS inside
                # a window.open("https://dl.fuckingfast.co/dl/...") call.
                # Regex captures the full URL including query strings/tokens,
                # stopping at the first quote or whitespace.
                download_url = await page.evaluate("""
                    () => {
                        const scripts = document.querySelectorAll('script');
                        for (const s of scripts) {
                            const text = s.textContent;
                            const match = text.match(/window\\.open\\(["'](https:\\/\\/dl\\.fuckingfast\\.co\\/[^"'\\s]+)["']/);
                            if (match) return match[1];
                        }
                        return null;
                    }
                """)

                if not download_url:
                    raise ValueError("URL not found in page JS")

                results.append({
                    "original": link,
                    "download_url": download_url,
                    "status": "ok",
                })
                print(f"  ✓ Found: {download_url}")

            except (ConnectionError, ValueError) as exc:
                results.append({
                    "original": link,
                    "download_url": None,
                    "status": "failed",
                })
                print(f"  ✗ Failed: {exc}")

            except Exception as exc:
                results.append({
                    "original": link,
                    "download_url": None,
                    "status": "failed",
                })
                print(f"  ✗ Failed (unexpected): {exc}")

            finally:
                await page.close()

        await browser.close()

    return results


# Quick manual test
if __name__ == "__main__":
    from links import LINKS

    extracted = asyncio.run(extract_download_urls(LINKS))
    for entry in extracted:
        print(entry)
