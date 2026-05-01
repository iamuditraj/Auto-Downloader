"""
Downloader module - uses aria2c's JSON-RPC daemon for parallel downloads
with real-time progress, speed display, and stall detection.
"""

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
import urllib.error

from logger.log_utils import log

# --- Config ---
MAX_CONCURRENT = 1
MAX_RETRIES = 2
STALL_TIMEOUT = 45        # seconds without progress before killing a download
RPC_PORT = 6800
RPC_SECRET = "autodl_secret"
RPC_URL = f"http://localhost:{RPC_PORT}/jsonrpc"
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "downloads")
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
)
REFERER = "https://fuckingfast.co"

ARIA2C_ERRORS = {
    3: "Resource not found (HTTP 404)",
    8: "Server returned bad response (token likely expired)",
    9: "Not enough disk space",
}
NO_RETRY_CODES = {3, 8, 9}


# ---------------------------------------------------------------------------
# RPC helpers
# ---------------------------------------------------------------------------

def _rpc(method: str, params: list = None) -> dict:
    """Send a JSON-RPC request to the aria2c daemon."""
    payload = json.dumps({
        "jsonrpc": "2.0",
        "id": "autodl",
        "method": method,
        "params": [f"token:{RPC_SECRET}"] + (params or []),
    }).encode()
    req = urllib.request.Request(
        RPC_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read())


def _add_url(url: str) -> str:
    """Add a URL to aria2c and return its GID."""
    result = _rpc("aria2.addUri", [[url], {
        "max-connection-per-server": "4",
        "split": "4",
        "file-allocation": "none",
        "max-tries": "1",          # retries handled by us
        "timeout": "60",
        "connect-timeout": "15",
        "dir": OUTPUT_DIR,
        "user-agent": USER_AGENT,
        "referer": REFERER,
    }])
    return result["result"]


def _get_status(gid: str) -> dict:
    """Return aria2c status dict for a GID."""
    result = _rpc("aria2.tellStatus", [gid, [
        "gid", "status", "totalLength", "completedLength",
        "downloadSpeed", "errorCode", "errorMessage", "files",
    ]])
    return result["result"]


def _remove(gid: str):
    """Force-remove a GID from aria2c (even if errored)."""
    try:
        _rpc("aria2.forceRemove", [gid])
    except Exception:
        pass
    try:
        _rpc("aria2.removeDownloadResult", [gid])
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Progress display
# ---------------------------------------------------------------------------

def _format_fname(fname: str) -> str:
    match = re.search(r'(part\d+)', fname, re.IGNORECASE)
    if match:
        return match.group(1)
    return fname


def _fmt_bytes(b: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if b < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} TB"


def _fmt_speed(bps: int) -> str:
    return f"{_fmt_bytes(bps)}/s"


def _progress_bar(done: int, total: int, width: int = 20) -> str:
    if total == 0:
        return "[" + "-" * width + "]   0%"
    pct = done / total
    filled = int(width * pct)
    bar = "█" * filled + "░" * (width - filled)
    return f"[{bar}] {pct*100:5.1f}%"


def _render_progress(active_jobs: list[dict]):
    """
    Redraw progress lines in-place using ANSI escape codes.
    Each job dict: { gid, url, status_dict, retry_count }
    """
    lines = []
    for job in active_jobs:
        s = job["status_dict"]
        total = int(s.get("totalLength", 0))
        done = int(s.get("completedLength", 0))
        speed = int(s.get("downloadSpeed", 0))
        fname = os.path.basename(s.get("files", [{}])[0].get("path", job["url"]))
        fname = _format_fname(fname)[:35].ljust(35)

        bar = _progress_bar(done, total)
        spd = _fmt_speed(speed)
        size = f"{_fmt_bytes(done)}/{_fmt_bytes(total)}" if total else "? / ?"

        retry_tag = f" [retry {job['retry_count']}/{MAX_RETRIES}]" if job["retry_count"] else ""
        lines.append(f"  {fname}  {bar}  {spd:>12}  {size}{retry_tag}")

    # Move cursor up by number of lines previously written, then overwrite
    if active_jobs:
        # \033[{n}A = move up n lines; \r = carriage return; \033[K = erase to EOL
        up = f"\033[{len(active_jobs)}A"
        sys.stdout.write(up)
        for line in lines:
            sys.stdout.write(f"\r\033[K{line}\n")
        sys.stdout.flush()


def _print_progress_header(n: int):
    """Print blank placeholder lines that _render_progress will overwrite."""
    for _ in range(n):
        print()


# ---------------------------------------------------------------------------
# Main download function
# ---------------------------------------------------------------------------

def download_files(extracted: list[dict]) -> dict:
    """
    Download files using an aria2c RPC daemon.

    Returns:
        dict: total, succeeded, failed, failed_urls
    """
    if not shutil.which("aria2c"):
        raise RuntimeError(
            "aria2c not found. Install from https://aria2.github.io/ and add to PATH."
        )

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    log(f"[INFO] Output directory: {OUTPUT_DIR}")

    # --- Build pending queue ---
    pending: list[dict] = []
    for entry in extracted:
        if entry.get("status") != "ok" or not entry.get("download_url"):
            log(f"[WARN] Skipping (status={entry.get('status')}): {entry.get('original')}")
            continue
        pending.append({"url": entry["download_url"], "retry_count": 0})

    if not pending:
        log("[INFO] No valid URLs to download.")
        return {"total": 0, "succeeded": 0, "failed": 0, "failed_urls": []}

    total_queued = len(pending)
    log(f"[INFO] {total_queued} file(s) queued.\n")

    # --- Start aria2c daemon ---
    daemon_cmd = [
        "aria2c",
        "--enable-rpc",
        f"--rpc-listen-port={RPC_PORT}",
        f"--rpc-secret={RPC_SECRET}",
        "--rpc-listen-all=false",
        f"--max-concurrent-downloads={MAX_CONCURRENT}",
        "--daemon=false",
        "--quiet=true",
    ]
    daemon = subprocess.Popen(
        daemon_cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    log(f"[INFO] aria2c RPC daemon started (PID {daemon.pid}, port {RPC_PORT})")

    def shutdown_daemon():
        try:
            _rpc("aria2.shutdown")
        except Exception:
            pass
        try:
            daemon.wait(timeout=5)
        except subprocess.TimeoutExpired:
            daemon.kill()

    # Handle Ctrl+C gracefully
    def _sigint_handler(sig, frame):
        log("\n[INFO] Interrupted — shutting down aria2c daemon...")
        shutdown_daemon()
        sys.exit(0)

    signal.signal(signal.SIGINT, _sigint_handler)

    # Wait for daemon to be ready
    for _ in range(20):
        time.sleep(0.3)
        try:
            _rpc("aria2.getVersion")
            break
        except Exception:
            continue
    else:
        daemon.kill()
        raise RuntimeError("aria2c RPC daemon did not start in time.")

    log("[INFO] aria2c RPC ready. Starting downloads...\n")

    # --- Tracking state ---
    # active_jobs: list of { gid, url, retry_count, status_dict, last_progress, last_progress_time }
    active_jobs: list[dict] = []
    succeeded = 0
    failed = 0
    failed_urls: list[str] = []

    def enqueue(url: str, retry_count: int = 0):
        gid = _add_url(url)
        active_jobs.append({
            "gid": gid,
            "url": url,
            "retry_count": retry_count,
            "status_dict": {},
            "last_progress": 0,
            "last_progress_time": time.time(),
        })

    # Fill initial slots
    while pending and len(active_jobs) < MAX_CONCURRENT:
        item = pending.pop(0)
        enqueue(item["url"], item["retry_count"])

    _print_progress_header(len(active_jobs))

    # --- Poll loop ---
    poll_interval = 1.0
    heartbeat_tick = 0

    while active_jobs or pending:
        time.sleep(poll_interval)
        heartbeat_tick += 1

        now = time.time()
        finished_indices = []
        messages_to_log = []

        for i, job in enumerate(active_jobs):
            try:
                s = _get_status(job["gid"])
            except Exception:
                continue

            job["status_dict"] = s
            status = s.get("status", "")
            done = int(s.get("completedLength", 0))

            # Stall detection: bytes haven't moved in STALL_TIMEOUT seconds
            if done > job["last_progress"]:
                job["last_progress"] = done
                job["last_progress_time"] = now
            elif status == "active" and (now - job["last_progress_time"]) > STALL_TIMEOUT:
                messages_to_log.append(f"\n[ STALL ] {job['url']} — no progress for {STALL_TIMEOUT}s, killing...")
                _remove(job["gid"])
                status = "stalled"
                job["status_dict"]["status"] = "stalled"

            if status in ("complete", "error", "stalled"):
                finished_indices.append(i)
                if status == "complete":
                    # Force completedLength to totalLength for final 100% render
                    total = int(s.get("totalLength", 0))
                    job["status_dict"]["completedLength"] = total
                    job["status_dict"]["downloadSpeed"] = 0

        # Render final state BEFORE logging, so the old block is visually 100%
        if finished_indices and active_jobs:
            _render_progress(active_jobs)

        # Process finished jobs
        for i in finished_indices:
            job = active_jobs[i]
            status = job["status_dict"].get("status", "")

            if status == "complete":
                succeeded += 1
                fname = os.path.basename(
                    job["status_dict"].get("files", [{}])[0].get("path", job["url"])
                )
                display_fname = _format_fname(fname)
                total = int(job["status_dict"].get("totalLength", 0))
                messages_to_log.append(f"\n[ DONE  ] {display_fname} ({_fmt_bytes(total)})")
            elif status != "stalled":
                s = job["status_dict"]
                err_code = int(s.get("errorCode", -1))
                err_msg = s.get("errorMessage", "unknown error")
                reason = ARIA2C_ERRORS.get(err_code, err_msg or f"exit code {err_code}")
                retries = job["retry_count"]

                if retries < MAX_RETRIES and err_code not in NO_RETRY_CODES:
                    messages_to_log.append(f"\n[ RETRY {retries+1}/{MAX_RETRIES} ] {job['url']} ({reason})")
                    pending.insert(0, {"url": job["url"], "retry_count": retries + 1})
                else:
                    failed += 1
                    failed_urls.append(job["url"])
                    messages_to_log.append(f"\n[ FAIL  ] {job['url']} — {reason}")

            if status != "stalled":
                _remove(job["gid"])

        # Remove finished jobs (reverse to preserve indices)
        for i in sorted(finished_indices, reverse=True):
            active_jobs.pop(i)

        # Fill freed slots
        while pending and len(active_jobs) < MAX_CONCURRENT:
            item = pending.pop(0)
            enqueue(item["url"], item["retry_count"])

        # Log messages and setup new progress block if needed
        for msg in messages_to_log:
            log(msg)
            
        if messages_to_log and active_jobs:
            _print_progress_header(len(active_jobs))

        # Re-draw progress for remaining active jobs
        if active_jobs:
            _render_progress(active_jobs)
        
        # Heartbeat log (every 60s) — goes to file only via logger, not stdout
        if heartbeat_tick % 60 == 0:
            import logging
            logging.getLogger("autodownloader").info(
                f"[HEARTBEAT] {len(active_jobs)} active, {len(pending)} pending"
            )

    # --- Cleanup ---
    shutdown_daemon()

    log(f"\n[INFO] All downloads complete. Files saved to: {OUTPUT_DIR}")
    return {
        "total": total_queued,
        "succeeded": succeeded,
        "failed": failed,
        "failed_urls": failed_urls,
    }


# Quick manual test
if __name__ == "__main__":
    test_data = [
        {"original": "https://example.com", "download_url": "https://dl.fuckingfast.co/dl/test", "status": "ok"},
        {"original": "https://example.com/bad", "download_url": None, "status": "failed"},
    ]
    download_files(test_data)