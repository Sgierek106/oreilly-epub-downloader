# O'Reilly EPUB Downloader

![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)
![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)

A CLI to download O'Reilly books as EPUB for offline reading. Uses cookie-based authentication to access your subscription content and generates clean EPUBs with images, cover art, and proper chapter structure.

## Installation

```bash
pip install -e .
```

## Usage

### 1. Export cookies from O'Reilly

1. Log into https://learning.oreilly.com in your browser
2. Open Developer Tools (Cmd+Option+I)
3. Go to Console and run:
   ```javascript
   JSON.stringify(Object.fromEntries(document.cookie.split('; ').map(c => c.split('='))))
   ```
4. Save the output to `cookies.json`

### 2. Download books

```bash
# By book ID
oreilly-dl 9781098166298 -c cookies.json

# By URL
oreilly-dl "https://learning.oreilly.com/library/view/ai-engineering/9781098166298/" -c cookies.json

# By Packt product URL (book ID is the numeric part at the end)
oreilly-dl "https://www.packtpub.com/product/Bare-Metal-Embedded-C-Programming/9781835460818" -c cookies.json

# Custom output path
oreilly-dl 9781098166298 -c cookies.json -o "My Book.epub"

# Download multiple books sequentially (one ID or URL per line)
oreilly-dl --book-file book-ids.txt -c cookies.json
```

Example output:

![Example output](assets/example.png)

Books are saved to `./downloads/` by default.

## Finding Book IDs

The book ID is the number in the O'Reilly URL:
- URL: `https://learning.oreilly.com/library/view/ai-engineering/9781098166298/`
- Book ID: `9781098166298`

You can also paste a Packt product URL — the book ID is the numeric part at the end:
- URL: `https://www.packtpub.com/product/Bare-Metal-Embedded-C-Programming/9781835460818`
- Book ID: `9781835460818`

## Refreshing Cookies

Cookies expire periodically. When downloads fail, re-export cookies from your browser.

## Requirements

- Python 3.11+
- Active O'Reilly Learning subscription
