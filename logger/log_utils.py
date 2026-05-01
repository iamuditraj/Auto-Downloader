"""
Shared logging utility — provides a single log() function
that writes to both console (print) and the session log file.
"""

import logging


def log(msg: str) -> None:
    """Print to console and write to the autodownloader log file."""
    print(msg)
    
    # Clean up newlines for the log file to prevent empty timestamped lines
    clean_msg = msg.strip('\n')
    if clean_msg:
        # Split multi-line messages so each gets a proper timestamp prefix
        for line in clean_msg.split('\n'):
            logging.getLogger("autodownloader").info(line)
