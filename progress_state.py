"""
Progress state management — tracks completed/failed downloads across sessions.
Persists state to progress.json for pause/resume support.
"""

import json
import os
from datetime import datetime

from logger.log_utils import log

PROGRESS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "progress.json")


def load_progress() -> dict:
    """Load progress state from progress.json. Returns empty state if missing or corrupt."""
    if not os.path.exists(PROGRESS_FILE):
        return {"completed": [], "failed": [], "last_updated": None}

    try:
        with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Validate structure
        if not isinstance(data.get("completed"), list):
            data["completed"] = []
        if not isinstance(data.get("failed"), list):
            data["failed"] = []
        return data

    except (json.JSONDecodeError, OSError) as e:
        log(f"[WARN] Could not read {PROGRESS_FILE}: {e}. Starting fresh.")
        return {"completed": [], "failed": [], "last_updated": None}


def save_progress(state: dict):
    """Write progress state to progress.json atomically (write-then-rename)."""
    state["last_updated"] = datetime.now().isoformat()
    tmp_path = PROGRESS_FILE + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp_path, PROGRESS_FILE)


def mark_completed(state: dict, url: str):
    """Add URL to completed list and save."""
    if url not in state["completed"]:
        state["completed"].append(url)
    # Remove from failed if it was there (retry succeeded)
    if url in state["failed"]:
        state["failed"].remove(url)
    save_progress(state)


def mark_failed(state: dict, url: str):
    """Add URL to failed list and save."""
    if url not in state["failed"]:
        state["failed"].append(url)
    save_progress(state)


def get_remaining(all_links: list, state: dict) -> list:
    """Return links that are not in completed or failed."""
    done = set(state["completed"]) | set(state["failed"])
    return [link for link in all_links if link not in done]


def reset_progress():
    """Delete progress.json for a fresh start."""
    if os.path.exists(PROGRESS_FILE):
        os.remove(PROGRESS_FILE)
        log(f"[INFO] Progress reset. Deleted {PROGRESS_FILE}")
    else:
        log("[INFO] No progress file found. Nothing to reset.")
