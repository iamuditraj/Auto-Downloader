"""
Logger setup - configures the shared "autodownloader" logger with a file handler.
"""

import logging
import os
from datetime import datetime

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
LOG_FILE = os.path.join(LOG_DIR, f"logs_{timestamp}.txt")


def setup_logger() -> str:
    """
    Set up the "autodownloader" logger with a file handler.
    Idempotent — won't add duplicate handlers if called multiple times.

    Returns:
        Path to the log file.
    """
    logger = logging.getLogger("autodownloader")

    # Skip if already configured
    if logger.handlers:
        return LOG_FILE

    logger.setLevel(logging.INFO)

    os.makedirs(LOG_DIR, exist_ok=True)
    file_handler = logging.FileHandler(LOG_FILE, mode="a", encoding="utf-8")
    file_handler.setFormatter(
        logging.Formatter("[%(asctime)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    )
    logger.addHandler(file_handler)

    return LOG_FILE
