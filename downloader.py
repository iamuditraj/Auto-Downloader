"""
Downloader module - handles downloading files via aria2c subprocess.
Manages a parallel queue of up to 5 concurrent downloads.
"""

import os
import shutil
import subprocess
import time

from log_utils import log

MAX_CONCURRENT = 1
MAX_RETRIES = 2
PROCESS_TIMEOUT = 600  # Kill aria2c if it hasn't exited after 10 minutes
OUTPUT_DIR = os.path.join(os.path.splitdrive(os.path.abspath(__file__))[0] + os.sep, "downloads")
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
)
REFERER = "https://fuckingfast.co"

# Human-readable aria2c exit code messages
ARIA2C_ERRORS = {
    3: "Resource not found (HTTP 404)",
    8: "Server returned bad response (token likely expired)",
    9: "Not enough disk space",
}

# Exit codes that should NOT be retried (retrying won't help)
NO_RETRY_CODES = {3, 8, 9}


def download_files(extracted: list[dict]) -> dict:
    """
    Download files using aria2c with up to MAX_CONCURRENT parallel slots.

    Args:
        extracted: List of dicts from extractor, each with keys:
                   "original", "download_url", "status"

    Returns:
        dict with keys: "total", "succeeded", "failed", "failed_urls"
    """
    # --- Preflight: ensure aria2c is available ---
    if not shutil.which("aria2c"):
        raise RuntimeError(
            "aria2c not found on PATH. "
            "Install it from https://aria2.github.io/ and ensure it's in your PATH."
        )

    # --- Prepare output directory ---
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    log(f"[INFO] Download directory: {OUTPUT_DIR}")

    # --- Build the pending queue, skipping invalid entries ---
    pending: list[str] = []
    for entry in extracted:
        if entry.get("status") != "ok" or not entry.get("download_url"):
            log(f"[WARN] Skipping (status={entry.get('status')}): {entry.get('original')}")
            continue
        pending.append(entry["download_url"])

    if not pending:
        log("[INFO] No valid URLs to download.")
        return {"total": 0, "succeeded": 0, "failed": 0, "failed_urls": []}

    total_queued = len(pending)
    succeeded = 0
    failed = 0
    failed_urls: list[str] = []
    retry_counts: dict[str, int] = {}  # url -> number of retries attempted
    log(f"[INFO] {total_queued} file(s) queued for download.\n")

    # --- Active process tracking: list of (Popen, url, slot_number, start_time) ---
    active: list[tuple[subprocess.Popen, str, int, float]] = []

    def start_download(url: str, slot: int) -> tuple[subprocess.Popen, float]:
        """Launch an aria2c subprocess for the given URL."""
        cmd = [
            "aria2c",
            "--continue=true",
            "--max-connection-per-server=1",
            "--split=1",
            "--file-allocation=none",
            "--max-tries=3",
            "--retry-wait=2",
            f"--dir={OUTPUT_DIR}",
            f"--user-agent={USER_AGENT}",
            f"--referer={REFERER}",
            "--timeout=30",
            url,
        ]
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        log(f"[ START ] (slot {slot}) {url}")
        return proc, time.time()

    def log_queue_state():
        """Print current queue status."""
        log(f"         → Active: {len(active)} | Pending: {len(pending)}")

    # --- Fill initial slots ---
    slot_counter = 0
    while pending and len(active) < MAX_CONCURRENT:
        url = pending.pop(0)
        slot_counter += 1
        proc, started = start_download(url, slot_counter)
        active.append((proc, url, slot_counter, started))

    log_queue_state()

    # --- Poll loop ---
    poll_tick = 0
    while active:
        time.sleep(1)
        poll_tick += 1

        # Heartbeat: show active slots every 30 seconds
        if poll_tick % 30 == 0:
            log(f"[HEARTBEAT] {len(active)} active, {len(pending)} pending — still downloading...")

        finished = []
        now = time.time()
        for i, (proc, url, slot, started) in enumerate(active):
            retcode = proc.poll()

            # Kill hung processes that exceed the timeout
            if retcode is None and (now - started) > PROCESS_TIMEOUT:
                log(f"[ TIMEOUT ] {url} — killing after {PROCESS_TIMEOUT}s")
                proc.kill()
                retcode = proc.wait()

            if retcode is not None:
                finished.append(i)
                if retcode == 0:
                    succeeded += 1
                    log(f"[ DONE  ] {url}")
                else:
                    reason = ARIA2C_ERRORS.get(retcode, f"exit code: {retcode}")
                    attempts = retry_counts.get(url, 0)
                    if attempts < MAX_RETRIES and retcode not in NO_RETRY_CODES:
                        retry_counts[url] = attempts + 1
                        log(f"[ RETRY {retry_counts[url]}/{MAX_RETRIES} ] {url} ({reason})")
                        # Re-queue for retry — picked up naturally on next poll cycle
                        pending.append(url)
                    else:
                        failed += 1
                        failed_urls.append(url)
                        log(f"[ PERM FAIL ] {url} — giving up after {attempts + 1} attempts")

        # Remove finished processes (reverse order to preserve indices)
        if finished:
            for i in sorted(finished, reverse=True):
                active.pop(i)

            # Fill freed slots with pending URLs
            while pending and len(active) < MAX_CONCURRENT:
                url = pending.pop(0)
                slot_counter += 1
                proc, started = start_download(url, slot_counter)
                active.append((proc, url, slot_counter, started))

            log_queue_state()

    log(f"\n[INFO] All downloads complete. Files saved to: {OUTPUT_DIR}")
    return {"total": total_queued, "succeeded": succeeded, "failed": failed, "failed_urls": failed_urls}


# Quick manual test
if __name__ == "__main__":
    test_data = [
        {
            "original": "https://example.com",
            "download_url": "https://dl.fuckingfast.co/dl/test",
            "status": "ok",
        },
        {
            "original": "https://example.com/bad",
            "download_url": None,
            "status": "failed",
        },
    ]
    download_files(test_data)
