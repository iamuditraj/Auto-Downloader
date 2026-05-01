"""
Links module - automatically reads URLs from links.txt.
Just paste your raw links into links.txt, one per line!
"""

import os

# Path to the links.txt file
LINKS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "links.txt")

LINKS = []

if os.path.exists(LINKS_FILE):
    with open(LINKS_FILE, "r", encoding="utf-8") as f:
        # Read lines, strip whitespace, and ignore empty lines or comments
        LINKS = [
            line.strip() 
            for line in f 
            if line.strip() and not line.strip().startswith("#")
        ]
else:
    print(f"[WARN] {LINKS_FILE} not found. Please create it and paste your links.")
