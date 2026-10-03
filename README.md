# O'Reilly EPUB Downloader

![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)
![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)

A CLI to download O'Reilly books as EPUB for offline reading. Uses cookie-based authentication to access your subscription content and generates clean EPUBs with images, cover art, and proper chapter structure.

## Installation

```bash
pip install -e .
```

## Usage

### 1. Authenticate

**Recommended: read cookies straight from your browser.** Log into
https://learning.oreilly.com in any supported browser (Chrome, Chromium,
Brave, Edge, Opera, Firefox, LibreWolf, Vivaldi, Arc) and pass
`--from-browser` — the tool reads the session cookies from the browser's own
cookie store, so there's nothing to export and nothing to keep in a file:

```bash
oreilly-dl 9781098166298 --from-browser
```

By default every installed browser is tried; pin one with `--browser`
(e.g. `--browser chromium`). On Linux, Chromium-based browsers encrypt
their cookies with the Secret Service keyring (gnome-keyring/kwallet) —
the first read may show a one-time unlock prompt. Firefox stores cookies
unencrypted unless a primary password is set.

**Institutional / proxy access works too.** If you log in through an
OCLC IdM (OpenAthens-style) proxy — e.g.
`https://learning-oreilly-com.<library>.idm.oclc.org` — the session
cookie is stored under the proxy's domain (`.idm.oclc.org`) rather than
`.oreilly.com`. The tool harvests both domains, and the JWT authenticates
against `learning.oreilly.com` directly, so `--from-browser` works with
no extra configuration.

**Alternative: a cookies file.** If you prefer the manual export, log in,
open DevTools (Cmd+Option+I) > Console, run
`JSON.stringify(Object.fromEntries(document.cookie.split('; ').map(c => c.split('='))))`
and save the output to `cookies.json`, then pass `-c cookies.json`.

### 2. Download books

```bash
# By book ID
oreilly-dl 9781098166298 --from-browser

# By URL
oreilly-dl "https://learning.oreilly.com/library/view/ai-engineering/9781098166298/" --from-browser

# By Packt product URL (book ID is the numeric part at the end)
oreilly-dl "https://www.packtpub.com/product/Bare-Metal-Embedded-C-Programming/9781835460818" --from-browser

# Custom output path
oreilly-dl 9781098166298 --from-browser -o "My Book.epub"

# Download multiple books sequentially (one ID or URL per line)
oreilly-dl --book-file book-ids.txt --from-browser
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

The session token (`orm-jwt`) is short-lived, so a long batch run can
outlive it. The tool checks the token's expiry before every book. If it has
expired, it automatically re-reads fresh cookies from your browser — a
logged-in browser keeps the token refreshed while you use the site — and
hot-swaps them into the running download. With `--from-browser` you should
rarely have to think about expiry at all; just keep a logged-in browser
around.

If you're using `-c cookies.json` and the file goes stale, the browser
fallback kicks in too. Only if that also fails does the tool halt with
manual refresh instructions.

## Requirements

- Python 3.11+
- Active O'Reilly Learning subscription
