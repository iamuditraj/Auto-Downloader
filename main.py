"""
Auto Downloader - Entry Point
Automates browser-based link extraction and file downloading.
"""

import asyncio
import sys
import time
from datetime import datetime

from links import LINKS
from extractor import extract_download_urls
from downloader import download_files
from logger_setup import setup_logger
from log_utils import log


def main():
    LOG_FILE = setup_logger()
    start_time = time.time()

    log(f"\n{'=' * 60}")
    log(f"  NEW SESSION — {datetime.today().strftime('%Y-%m-%d %H:%M:%S')}")
    log(f"{'=' * 60}")

    total_links = len(LINKS)
    log(f"Total links loaded: {total_links}")
    log(f"Starting extraction for {total_links} link(s)...\n")

    # --- Step 1: Extract real download URLs ---
    extracted = asyncio.run(extract_download_urls(LINKS))

    # --- Step 2: Print summary ---
    ok = [e for e in extracted if e["status"] == "ok"]
    failed_extract = [e for e in extracted if e["status"] != "ok"]

    log(f"\n{'=' * 60}")
    log(f"  Extraction complete: {len(ok)} succeeded, {len(failed_extract)} failed")
    log(f"{'=' * 60}\n")

    for entry in extracted:
        if entry["status"] == "ok":
            log(f"  ✓ {entry['original']}")
            log(f"    → {entry['download_url']}\n")
        else:
            log(f"  ✗ {entry['original']}")
            log(f"    → FAILED\n")

    # --- Step 3: Dry-run check ---
    if "--dry-run" in sys.argv:
        log("Dry run complete. No files were downloaded.")
        elapsed = time.time() - start_time
        log(f"Time elapsed: {elapsed:.1f}s")
        return

    # --- Step 4: Download ---
    log("Starting downloads...\n")
    dl_stats = download_files(extracted)

    # --- Step 5: Final summary ---
    elapsed = time.time() - start_time

    log(f"\n{'=' * 60}")
    log(f"  FINAL SUMMARY")
    log(f"  Total links processed : {total_links}")
    log(f"  Downloads attempted   : {dl_stats['total']}")
    log(f"  Succeeded             : {dl_stats['succeeded']}")
    log(f"  Failed                : {dl_stats['failed']}")

    if dl_stats.get("failed_urls"):
        log(f"  Failed URLs:")
        for url in dl_stats["failed_urls"]:
            log(f"    • {url}")

    log(f"  Time elapsed          : {elapsed:.1f}s")
    log(f"{'=' * 60}")
    log("\nAll done.")
    log(f"Session log saved to: {LOG_FILE}")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as e:
        log(f"\n[ERROR] {e}")
        sys.exit(1)
