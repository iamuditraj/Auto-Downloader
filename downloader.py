"""
Downloader module - handles downloading files via aria2c subprocess.
Manages a parallel queue of up to 5 concurrent downloads.
"""

import os
import shutil
import subprocess
import time

from log_utils import log

MAX_CONCURRENT = 5
MAX_RETRIES = 2
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

    # --- Active process tracking: list of (Popen, url, slot_number) ---
    active: list[tuple[subprocess.Popen, str, int]] = []

    def start_download(url: str, slot: int) -> subprocess.Popen:
        """Launch an aria2c subprocess for the given URL."""
        cmd = [
            "aria2c",
            "--continue=true",
            "--max-connection-per-server=4",
            "--split=4",
            "--file-allocation=none",
            f"--dir={OUTPUT_DIR}",
            f"--user-agent={USER_AGENT}",
            f"--referer={REFERER}",
            "--timeout=60",
            url,
        ]
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        log(f"[ START ] (slot {slot}) {url}")
        return proc

    def log_queue_state():
        """Print current queue status."""
        log(f"         → Active: {len(active)} | Pending: {len(pending)}")

    # --- Fill initial slots ---
    slot_counter = 0
    while pending and len(active) < MAX_CONCURRENT:
        url = pending.pop(0)
        slot_counter += 1
        proc = start_download(url, slot_counter)
        active.append((proc, url, slot_counter))

    log_queue_state()

    # --- Poll loop ---
    while active:
        time.sleep(1)

        finished = []
        for i, (proc, url, slot) in enumerate(active):
            retcode = proc.poll()
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
                proc = start_download(url, slot_counter)
                active.append((proc, url, slot_counter))

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
