"""
Downloader module - uses aria2c's JSON-RPC daemon for single-file downloads
with real-time progress, speed display, and stall detection.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request

from logger.log_utils import log

# --- Config ---
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

        lines.append(f"  {fname}  {bar}  {spd:>12}  {size}")

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
# Single-file download (for pause/resume one-at-a-time flow)
# ---------------------------------------------------------------------------

def download_single_file(entry: dict) -> dict:
    """
    Download a single file using an aria2c RPC daemon.
    Starts and stops the daemon for this one file.

    Args:
        entry: dict with "original", "download_url", "status" keys

    Returns:
        dict: { "succeeded": bool, "url": str, "error": str|None }
    """
    url = entry.get("download_url")
    if not url:
        return {"succeeded": False, "url": "", "error": "No download URL"}

    if not shutil.which("aria2c"):
        raise RuntimeError(
            "aria2c not found. Install from https://aria2.github.io/ and add to PATH."
        )

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Try to shut down any orphan daemon from a previous interrupted run
    try:
        _rpc("aria2.shutdown")
        time.sleep(1)
    except Exception:
        pass

    # --- Start aria2c daemon ---
    daemon_cmd = [
        "aria2c",
        "--enable-rpc",
        f"--rpc-listen-port={RPC_PORT}",
        f"--rpc-secret={RPC_SECRET}",
        "--rpc-listen-all=false",
        "--max-concurrent-downloads=1",
        "--daemon=false",
        "--quiet=true",
    ]
    daemon = subprocess.Popen(
        daemon_cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    def shutdown():
        try:
            _rpc("aria2.shutdown")
        except Exception:
            pass
        try:
            daemon.wait(timeout=5)
        except subprocess.TimeoutExpired:
            daemon.kill()

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
        return {"succeeded": False, "url": url, "error": "aria2c daemon did not start"}

    result = {"succeeded": False, "url": url, "error": None}

    try:
        # Add URL to aria2c
        gid = _add_url(url)

        job = {
            "gid": gid,
            "url": url,
            "retry_count": 0,
            "status_dict": {},
            "last_progress": 0,
            "last_progress_time": time.time(),
        }

        _print_progress_header(1)

        # --- Poll loop ---
        while True:
            time.sleep(1.0)

            try:
                s = _get_status(gid)
            except Exception:
                continue

            job["status_dict"] = s
            status = s.get("status", "")
            done = int(s.get("completedLength", 0))
            now = time.time()

            # Stall detection
            if done > job["last_progress"]:
                job["last_progress"] = done
                job["last_progress_time"] = now
            elif status == "active" and (now - job["last_progress_time"]) > STALL_TIMEOUT:
                log(f"\n[ STALL ] Download stalled for {STALL_TIMEOUT}s, killing...")
                _remove(gid)
                result["error"] = f"Download stalled for {STALL_TIMEOUT}s"
                break

            # Render progress
            _render_progress([job])

            if status == "complete":
                # Force 100% for final render
                total = int(s.get("totalLength", 0))
                job["status_dict"]["completedLength"] = total
                job["status_dict"]["downloadSpeed"] = 0
                _render_progress([job])

                fname = os.path.basename(
                    s.get("files", [{}])[0].get("path", url)
                )
                display_fname = _format_fname(fname)
                log(f"\n[ DONE  ] {display_fname} ({_fmt_bytes(total)})")
                result["succeeded"] = True
                _remove(gid)
                break

            elif status == "error":
                err_code = int(s.get("errorCode", -1))
                err_msg = s.get("errorMessage", "unknown error")
                reason = ARIA2C_ERRORS.get(err_code, err_msg or f"exit code {err_code}")
                log(f"\n[ FAIL  ] {reason}")
                result["error"] = reason
                _remove(gid)
                break

    except KeyboardInterrupt:
        log("\n[INFO] Interrupted — shutting down aria2c daemon...")
        shutdown()
        raise

    finally:
        shutdown()

    return result


# Quick manual test
if __name__ == "__main__":
    test_data = {
        "original": "https://example.com",
        "download_url": "https://dl.fuckingfast.co/dl/test",
        "status": "ok",
    }
    download_single_file(test_data)