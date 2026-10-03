"""Command-line interface for O'Reilly book downloader."""

import re
import sys
from pathlib import Path

import click
from rich.console import Console

from .client import OreillyClient
from .cookie_auth import (
    SUPPORTED_BROWSERS,
    BrowserCookieError,
    CookieExpiredError,
    Session,
    check_cookie_expiry,
    load_cookies,
    load_cookies_from_browser,
)
from .epub import create_epub

console = Console()


def extract_book_id(book_input: str) -> str:
    """Extract book ID from a URL or direct input.

    Handles O'Reilly learning URLs, Packt product URLs (and other URLs where
    the book ID is the numeric part at the end), raw book IDs, and ISBN-like
    numbers.
    """
    # O'Reilly learning URL, e.g. .../library/view/<slug>/<book-id>/
    oreilly_match = re.search(r"learning\.oreilly\.com/library/view/[^/]+/(\d+)", book_input)
    if oreilly_match:
        return oreilly_match.group(1)

    # Direct numeric input
    if re.match(r"^\d+$", book_input):
        return book_input

    # Generic URL: the numeric part at the end of the URL,
    # e.g. https://www.packtpub.com/product/<title>/<book-id>
    url_match = re.search(r"https?://\S*/(\d+)", book_input)
    if url_match:
        return url_match.group(1)

    # ISBN-like number anywhere in the input
    isbn_match = re.search(r"(\d{10,13})", book_input)
    if isbn_match:
        return isbn_match.group(1)

    return book_input


def sanitize_filename(name: str) -> str:
    """Create a safe filename from book title."""
    safe = re.sub(r'[<>:"/\\|?*]', "", name)
    safe = re.sub(r"\s+", " ", safe).strip()
    return safe[:100]


def require_valid_cookies(
    session: Session, client: OreillyClient, browser: str | None
) -> None:
    """Halt with refresh instructions if the session cookie has expired.

    Called before each book download: the token is short-lived, so a long
    batch run can outlive it. Without this per-book check the API would
    silently return truncated preview content for the remaining books.

    On expiry, first tries to re-read fresh cookies from the local browser
    (a logged-in browser keeps the session token refreshed while you use
    the site) and hot-swaps them into the live client. Only if that also
    fails do we halt with manual instructions.
    """
    try:
        check_cookie_expiry(session)
        return
    except CookieExpiredError as e:
        console.print(f"\n[yellow]Cookies expired:[/] {e}")

    try:
        fresh = load_cookies_from_browser(browser)
        check_cookie_expiry(fresh)
    except (BrowserCookieError, CookieExpiredError) as e:
        console.print(f"[yellow]Could not refresh from browser:[/] {e}")
        console.print(
            "[yellow]Open https://learning.oreilly.com in your browser and log "
            "in — the session token refreshes while you browse — then try again.\n"
            "Alternatively, extract cookies manually: open DevTools "
            "(Cmd+Option+I) > Console and run:\n"
            "  JSON.stringify(Object.fromEntries("
            "document.cookie.split('; ').map(c => c.split('='))))\n"
            "Then save the output to your cookies file and try again.[/]"
        )
        sys.exit(1)

    console.print(f"[green]Refreshed cookies from {fresh.source}[/]")
    session.cookies = fresh.cookies
    client.update_session(session)


@click.command()
@click.argument("book", required=False)
@click.option(
    "-c",
    "--cookies",
    type=click.Path(exists=True, path_type=Path),
    help="Path to cookies.json file",
)
@click.option(
    "--from-browser",
    is_flag=True,
    help="Read cookies from your local browser instead of a file",
)
@click.option(
    "--browser",
    type=click.Choice(SUPPORTED_BROWSERS),
    default=None,
    help="Which browser to read cookies from (default: auto-detect)",
)
@click.option(
    "-o",
    "--output",
    type=click.Path(path_type=Path),
    help="Output path (defaults to ./downloads/<title>.epub)",
)
@click.option(
    "-f",
    "--book-file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Path to a text file containing one book ID or URL per line",
)
def main(
    book: str | None,
    cookies: Path | None,
    from_browser: bool,
    browser: str | None,
    output: Path | None,
    book_file: Path | None,
) -> None:
    """Download O'Reilly books as EPUB.

    BOOK can be a book ID or a URL (O'Reilly learning or Packt product URLs).
    Use --book-file to download multiple books sequentially from a text file
    containing one book ID or URL per line.

    \b
    Examples:
        oreilly-dl 9781098166298 --from-browser
        oreilly-dl 9781098166298 -c cookies.json
        oreilly-dl "https://learning.oreilly.com/library/view/book/9781098166298/" --from-browser
        oreilly-dl "https://www.packtpub.com/product/Bare-Metal-Embedded-C-Programming/9781835460818" --from-browser --browser chromium
        oreilly-dl --book-file book-ids.txt --from-browser
    """
    try:
        if book and book_file:
            raise click.UsageError("BOOK and --book-file cannot be used together")
        if not book and not book_file:
            raise click.UsageError("provide BOOK or --book-file")
        if book_file and output:
            raise click.UsageError("--output cannot be used with --book-file")
        if from_browser and cookies:
            raise click.UsageError(
                "--cookies and --from-browser cannot be used together"
            )

        if book_file:
            books = [line.strip() for line in book_file.read_text().splitlines()]
            books = [book for book in books if book and not book.startswith("#")]
            if not books:
                raise click.UsageError(f"{book_file} does not contain any book IDs")
        else:
            books = [book]

        if from_browser or not cookies:
            session = load_cookies_from_browser(browser)
        else:
            session = load_cookies(cookies)

        with OreillyClient(session) as client:
            for book_input in books:
                # Re-check before each book: the token is short-lived, so a
                # long batch run can outlive it. Without this, the API
                # silently returns truncated preview content for the rest.
                require_valid_cookies(session, client, browser)

                book_id = extract_book_id(book_input)
                console.print(f"[bold]Downloading book:[/] {book_id}")
                book_data = client.get_book(book_id)

                if output:
                    output_path = output if output.suffix == ".epub" else output.with_suffix(".epub")
                else:
                    downloads = Path("~/Downloads").expanduser()
                    downloads.mkdir(exist_ok=True)
                    safe_title = sanitize_filename(book_data.metadata.title)
                    output_path = downloads / f"{safe_title}-{book_data.metadata.isbn}.epub"

                create_epub(book_data, output_path)
                console.print(f"\n[bold green]Done:[/] {output_path}")

    except KeyboardInterrupt:
        console.print("\n[yellow]Cancelled[/]")
        sys.exit(130)
    except Exception as e:
        console.print(f"\n[bold red]Error:[/] {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
