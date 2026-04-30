"""
Shared logging utility — provides a single log() function
that writes to both console (print) and the session log file.
"""

import logging


def log(msg: str) -> None:
    """Print to console and write to the autodownloader log file."""
    print(msg)
    logging.getLogger("autodownloader").info(msg)
