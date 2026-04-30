# Auto Downloader

A Python-based web automation tool that uses **Playwright** to extract direct download links from [fuckingfast.co](https://fuckingfast.co) pages and download the files automatically.

## What It Does

1. Reads a list of fuckingfast.co URLs from `links.py`
2. Uses browser automation (Playwright) to visit each page and extract the real download URL
3. Downloads the files to your local machine

## Project Structure

```
Auto Downloader/
├── main.py           # Entry point - orchestrates the workflow
├── links.py          # List of URLs to process
├── extractor.py      # Browser automation to extract download links
├── downloader.py     # File download logic
├── requirements.txt  # Python dependencies
└── README.md         # This file
```

## Setup

### 1. Install Python Dependencies

```bash
pip install -r requirements.txt
```

### 2. Install Playwright Browser

```bash
playwright install chromium
```

### 3. Run the Script

```bash
python main.py
```
