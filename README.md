# 🚀 Auto Downloader

A high-performance, automated tool designed to extract and download files from web-based links using **Playwright** for browser automation and **aria2c** for multi-threaded, parallel downloading.

---

## ✨ Features

*   **Smart Extraction**: Uses Playwright to bypass intermediate pages and extract direct download links.
*   **Stable Downloads**: Downloads files sequentially (1 active download) to avoid host throttling, but uses `aria2c` to download each file in parallel chunks.
*   **Resilient**: Automatic retries for failed downloads (up to 2 retries per file) and stall detection (45s timeout).
*   **Optimized Performance**: Uses `aria2c` with multi-connection (4 connections per server) and split-file downloading.
*   **Clean UI**: Real-time terminal progress with smart filename shortening (e.g. displaying just `part006`) to keep the console tidy.
*   **Detailed Logging**: Maintains per-session log files with full execution history.
*   **Dry Run Support**: Test link extraction without initiating large downloads.

---

## 🛠️ Project Structure

```text
Auto-Downloader/
├── main.py           # Entry point — orchestrates the workflow
├── links.py          # Configuration — add your source URLs here
├── extractor.py      # Browser automation engine (Playwright)
├── downloader.py     # Download engine (aria2c wrapper)
├── logger_setup.py   # Logging configuration
├── log_utils.py      # Logging utility functions
├── requirements.txt  # Python dependencies
└── README.md         # This file
```

---

## 🚀 Getting Started

### 1. Prerequisites

*   **Python 3.8+**
*   **aria2c**: Must be installed and available in your system `PATH`.
    *   [Download aria2c](https://aria2.github.io/)

### 2. Installation

Clone the repository and set up the environment:

```bash
# Create and activate virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Install Playwright browser
playwright install chromium
```

### 3. Usage

1.  Open `links.py` and add your target URLs to the `LINKS` list.
2.  Run the main script:

```bash
python main.py
```

#### Options
*   **Dry Run**: To verify link extraction without downloading, run:
    ```bash
    python main.py --dry-run
    ```

---

## ⚙️ Configuration

You can customize the download behavior in `downloader.py`:
*   `MAX_CONCURRENT`: Number of parallel download slots (Default: `1` for stability).
*   `MAX_RETRIES`: Number of retry attempts for failed downloads (Default: `2`).
*   `STALL_TIMEOUT`: Seconds without progress before restarting a download (Default: `45`).
*   `OUTPUT_DIR`: Where files are saved (Default: `downloads/` folder within the project).

---

## 📝 Logging

Every session is logged with a timestamped summary. A brand new log file is automatically generated for every run (e.g., `logs_2026-05-01_22-48-51.txt`) inside the `logs/` directory. The log includes:
*   Original vs. Extracted URLs.
*   Download success/failure status.
*   Elapsed time and error details.

---

## ⚖️ License

This project is for educational and automation purposes. Please ensure you comply with the terms of service of the websites you are interacting with.

