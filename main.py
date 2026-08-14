"""
Auto Downloader - Entry Point
Automates browser-based link extraction and file downloading.
Supports pause/resume via progress tracking and 'p' + Enter input.
"""


import asyncio
import sys
import threading
import time
from datetime import datetime

from links import LINKS
from extractor import extract_single_url
from downloader import download_single_file
from progress_state import load_progress, mark_completed, mark_failed, get_remaining, reset_progress, clear_failed
from logger.logger_setup import setup_logger
from logger.log_utils import log

MAX_FILE_RETRIES = 3

# --- Pause flag (set by background listener thread) ---
pause_requested = False


def _pause_listener():
    """Background thread: waits for 'p' + Enter to set pause flag."""
    global pause_requested
    while not pause_requested:
        try:
            user_input = input()
            if user_input.strip().lower() in ('p', 'pause'):
                pause_requested = True
                log("\n[PAUSE] Pause requested — will stop after current file completes...")
        except EOFError:
            break


async def async_main():
    global pause_requested

    # Handle --reset flag
    if "--reset" in sys.argv:
        reset_progress()
        return

    LOG_FILE = setup_logger()
    start_time = time.time()

    log(f"\n{'=' * 60}")
    log(f"  AUTO DOWNLOADER — {datetime.today().strftime('%Y-%m-%d %H:%M:%S')}")
    log(f"{'=' * 60}")

    state = load_progress()

    # Handle --status flag
    if "--status" in sys.argv:
        remaining = get_remaining(LINKS, state)
        log(f"  Total links   : {len(LINKS)}")
        log(f"  Completed     : {len(state['completed'])}")
        log(f"  Failed        : {len(state['failed'])}")
        log(f"  Remaining     : {len(remaining)}")
        return

    # Clear previously failed links so they are retried this session
    clear_failed(state)

    remaining = get_remaining(LINKS, state)
    total_links = len(LINKS)

    log(f"  Total links   : {total_links}")
    log(f"  Completed     : {len(state['completed'])}")
    log(f"  Failed        : {len(state['failed'])}")
    log(f"  Remaining     : {len(remaining)}")
    log(f"{'=' * 60}")

    if not remaining:
        log("\nAll files already downloaded! Use --reset to start fresh.")
        return

    log(f"\n  Type 'p' + Enter at any time to pause after the current file.\n")

    # Start pause listener thread
    listener = threading.Thread(target=_pause_listener, daemon=True)
    listener.start()

    # --- Process files one at a time ---
    succeeded = 0
    failed = 0
    failed_urls = []

    for i, link in enumerate(remaining):
        if pause_requested:
            log(f"\n[PAUSE] Stopped. {len(remaining) - i} file(s) remaining.")
            break

        log(f"\n{'—' * 60}")
        log(f"  [{i+1}/{len(remaining)}] {link}")
        log(f"{'—' * 60}")

        success = False

        for attempt in range(1, MAX_FILE_RETRIES + 1):
            if attempt > 1:
                log(f"\n  [RETRY] Attempt {attempt}/{MAX_FILE_RETRIES} — re-extracting token...")

            # --- Extract token for THIS file ---
            log(f"  Extracting download token...")
            result = await extract_single_url(link)

            if result["status"] != "ok":
                log(f"  [EXTRACT FAIL] Could not extract token (attempt {attempt}/{MAX_FILE_RETRIES})")
                continue

            log(f"  ✓ Token extracted. Starting download...")

            # --- Download THIS file ---
            dl_result = await download_single_file(result)

            if dl_result["succeeded"]:
                success = True
                break
            else:
                log(f"  [DOWNLOAD FAIL] {dl_result.get('error', 'unknown')} — attempt {attempt}/{MAX_FILE_RETRIES}")

        if success:
            succeeded += 1
            mark_completed(state, link)
        else:
            failed += 1
            failed_urls.append(link)
            mark_failed(state, link)
            log(f"  [SKIP] Failed after {MAX_FILE_RETRIES} attempts")

    # --- Final summary ---
    elapsed = time.time() - start_time

    log(f"\n{'=' * 60}")
    log(f"  SESSION SUMMARY")
    log(f"  Files processed       : {succeeded + failed}")
    log(f"  Succeeded             : {succeeded}")
    log(f"  Failed                : {failed}")

    if failed_urls:
        log(f"  Failed URLs:")
        for url in failed_urls:
            log(f"    • {url}")

    remaining_after = get_remaining(LINKS, state)
    if pause_requested and remaining_after:
        log(f"  Remaining (paused)    : {len(remaining_after)}")

    log(f"  Time elapsed          : {elapsed:.1f}s")
    log(f"{'=' * 60}")

    if remaining_after:
        log("\nRun main.py again to continue from where you left off.")
    else:
        log("\nAll done! All files downloaded.")

    log(f"Session log saved to: {LOG_FILE}")


if __name__ == "__main__":
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(async_main())
        loop.close()
    except KeyboardInterrupt:
        log(f"\n[FORCE STOP] Ctrl+C — session terminated immediately.")
        sys.exit(1)
    except RuntimeError as e:
        log(f"\n[ERROR] {e}")
        sys.exit(1)
