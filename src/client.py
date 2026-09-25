"""O'Reilly API client for fetching book content."""

import random
import re
import time
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from .cookie_auth import Session
from .models import Book, BookMetadata, Chapter, Image

console = Console()

API_BASE = "https://learning.oreilly.com/api/v2/"
CONTENT_BASE = "https://learning.oreilly.com/"


def human_delay(min_ms: int = 300, max_ms: int = 1500) -> None:
    """Add a random human-like delay between requests."""
    time.sleep(random.randint(min_ms, max_ms) / 1000)


class OreillyClient:
    """Client for interacting with O'Reilly Learning API."""

    def __init__(self, session: Session):
        self.session = session
        self.http = httpx.Client(
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "application/json, text/html, */*",
                "Accept-Language": "en-US,en;q=0.9",
                "Cookie": session.get_cookie_header(),
                "Referer": "https://learning.oreilly.com/",
            },
            follow_redirects=True,
            timeout=30.0,
        )

    def get_book(self, book_id: str) -> Book:
        """Fetch complete book with metadata and chapters.

        Args:
            book_id: The O'Reilly book ID (from URL)

        Returns:
            Complete Book object with metadata and chapter content
        """
        console.print(f"[bold]Fetching book: {book_id}[/]")

        # Get book metadata
        metadata = self._get_metadata(book_id)
        console.print(f"[green]Found:[/] {metadata}")

        # Get table of contents / chapters
        chapters = self._get_chapters(book_id)
        console.print(f"[green]Found {len(chapters)} chapters[/]")

        # Fetch chapter content
        self._fetch_chapter_content(chapters)

        # Collect and download images from all chapters
        images = self._fetch_images(chapters)
        console.print(f"[green]Downloaded {len(images)} images[/]")

        # The API does not expose authors, so recover them from the title page.
        if not metadata.authors:
            metadata.authors = self._extract_authors(chapters)

        # Fetch cover image. Prefer the dedicated cover URL; otherwise reuse the
        # cover image already downloaded from the "Cover" chapter.
        cover_image = self._fetch_cover(metadata.cover_url)
        if not cover_image:
            cover_image = self._find_cover_image(images)

        return Book(metadata=metadata, chapters=chapters, cover_image=cover_image, images=images)

    def _get_metadata(self, book_id: str) -> BookMetadata:
        """Fetch book metadata from API."""
        url = f"{API_BASE}epubs/urn:orm:book:{book_id}/"
        response = self.http.get(url)

        if response.status_code == 404:
            raise ValueError(f"Book not found: {book_id}")
        response.raise_for_status()

        data = response.json()

        # The API exposes the description under a "descriptions" dict keyed by
        # MIME type. Prefer plain text, falling back to stripped HTML.
        descriptions = data.get("descriptions", {}) or {}
        description = descriptions.get("text/plain", "")
        if not description:
            html_desc = descriptions.get("text/html", "")
            if html_desc:
                description = BeautifulSoup(html_desc, "lxml").get_text(
                    " ", strip=True
                )

        # The epub endpoint does not expose authors or a publisher. Authors are
        # recovered from the title page in get_book(); the publisher defaults to
        # O'Reilly (this is an O'Reilly downloader).
        cover_url = data.get("cover", "") or data.get("cover_url", "")

        return BookMetadata(
            id=book_id,
            title=data.get("title", "Unknown Title"),
            authors=[],
            publisher=data.get("publisher") or "O'Reilly Media, Inc.",
            description=description,
            cover_url=cover_url,
            isbn=data.get("isbn", ""),
            language=data.get("language", "en"),
        )

    def _get_chapters(self, book_id: str) -> list[Chapter]:
        """Fetch table of contents from the API."""
        console.print(f"[dim]Fetching table of contents...[/]")

        # Use the chapters API endpoint
        chapters_url = f"{API_BASE}epub-chapters/?epub_identifier=urn:orm:book:{book_id}"
        all_results = []

        while chapters_url:
            human_delay(500, 1000)

            response = self.http.get(chapters_url)
            if response.status_code != 200:
                console.print(f"[yellow]Chapters API returned {response.status_code}, trying fallback...[/]")
                return self._get_chapters_fallback(book_id)

            data = response.json()
            results = data.get("results", data) if isinstance(data, dict) else data
            all_results.extend(results)

            # Follow data.get("next") to get the next chapter
            chapters_url = data.get("next") if isinstance(data, dict) else None

        chapters = []
        for i, item in enumerate(all_results):
            title = item.get("title", f"Chapter {i + 1}")
            content_url = item.get("content_url", "")
            chapter_id = item.get("ourn", "").split(":")[-1].replace(".html", "") or f"ch{i}"

            # Skip cover and other front matter that we don't need
            # (we'll handle cover separately)

            chapters.append(
                Chapter(
                    id=chapter_id,
                    title=title,
                    url=item.get("url", ""),
                    content_url=content_url,
                    order=i,
                )
            )

        return chapters

    def _get_chapters_fallback(self, book_id: str) -> list[Chapter]:
        """Fallback: scrape chapter list from the book page."""
        book_page_url = f"{CONTENT_BASE}library/view/-/{book_id}/"

        human_delay(500, 1000)
        response = self.http.get(book_page_url)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "lxml")
        chapters = []

        # Find chapter links in the TOC section
        chapter_links = soup.select('a[href*="/library/view/"][href$=".html"]')

        seen_urls = set()
        for link in chapter_links:
            href = link.get("href", "")
            if not href or href in seen_urls:
                continue

            if book_id not in href:
                continue

            seen_urls.add(href)

            title = link.get_text(strip=True) or f"Chapter {len(chapters) + 1}"
            content_url = urljoin(CONTENT_BASE, href)
            chapter_id = href.split("/")[-1].replace(".html", "")

            chapters.append(
                Chapter(
                    id=chapter_id,
                    title=title,
                    url=href,
                    content_url=content_url,
                    order=len(chapters),
                )
            )

        return chapters

    def _fetch_chapter_content(self, chapters: list[Chapter]) -> None:
        """Fetch HTML content for all chapters with human-like pacing."""
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task("Downloading chapters...", total=len(chapters))

            for i, chapter in enumerate(chapters):
                progress.update(task, description=f"Downloading: {chapter.title[:40]}")

                if not chapter.content_url:
                    progress.advance(task)
                    continue

                # Add human-like delay between chapter requests
                # Vary delay more for early chapters, settle into rhythm
                if i < 3:
                    human_delay(1000, 2500)
                else:
                    human_delay(500, 1500)

                try:
                    response = self.http.get(chapter.content_url)
                    response.raise_for_status()

                    # Check if response is JSON (API) or HTML (direct content)
                    content_type = response.headers.get("content-type", "")

                    if "json" in content_type:
                        data = response.json()
                        chapter.html_content = data.get("content", "")
                    else:
                        chapter.html_content = response.text

                    # Clean up the HTML content
                    chapter.html_content = self._clean_html(chapter.html_content)

                except httpx.HTTPError as e:
                    console.print(
                        f"[yellow]Warning: Failed to fetch {chapter.title}: {e}[/]"
                    )
                    # On error, wait a bit longer before next request
                    human_delay(2000, 4000)

                progress.advance(task)

    def _fetch_images(self, chapters: list[Chapter]) -> dict[str, Image]:
        """Extract and download all images from chapters."""
        # First, collect all unique image URLs
        image_urls: set[str] = set()

        for chapter in chapters:
            if not chapter.html_content:
                continue

            soup = BeautifulSoup(chapter.html_content, "lxml")
            for img in soup.find_all("img"):
                src = img.get("src", "")
                if src and not src.startswith("data:"):
                    # Make absolute URL
                    if not src.startswith("http"):
                        src = urljoin(CONTENT_BASE, src)
                    image_urls.add(src)

        if not image_urls:
            return {}

        # Download images
        images: dict[str, Image] = {}

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task("Downloading images...", total=len(image_urls))

            for url in image_urls:
                progress.update(task, description=f"Image: {url.split('/')[-1][:30]}")

                try:
                    human_delay(100, 300)  # Shorter delay for images
                    response = self.http.get(url)
                    response.raise_for_status()

                    # Generate filename from URL
                    filename = url.split("/")[-1]
                    # Ensure it has an extension
                    if "." not in filename:
                        filename = f"{filename}.png"

                    # Determine media type
                    content_type = response.headers.get("content-type", "image/png")
                    if "jpeg" in content_type or "jpg" in content_type:
                        media_type = "image/jpeg"
                    elif "gif" in content_type:
                        media_type = "image/gif"
                    elif "svg" in content_type:
                        media_type = "image/svg+xml"
                    elif "webp" in content_type:
                        media_type = "image/webp"
                    else:
                        media_type = "image/png"

                    images[url] = Image(
                        url=url,
                        filename=f"images/{filename}",
                        data=response.content,
                        media_type=media_type,
                    )

                except httpx.HTTPError as e:
                    console.print(f"[yellow]Warning: Failed to fetch image {url}: {e}[/]")

                progress.advance(task)

        # Now update chapter HTML to use local image paths
        for chapter in chapters:
            if chapter.html_content:
                chapter.html_content = self._rewrite_image_urls(chapter.html_content, images)

        return images

    def _rewrite_image_urls(self, html: str, images: dict[str, Image]) -> str:
        """Rewrite image URLs in HTML to use local EPUB paths."""
        soup = BeautifulSoup(html, "lxml")

        for img in soup.find_all("img"):
            src = img.get("src", "")
            if not src or src.startswith("data:"):
                continue

            # Make absolute URL for lookup
            abs_url = src if src.startswith("http") else urljoin(CONTENT_BASE, src)

            if abs_url in images:
                img["src"] = images[abs_url].filename

        # Return the modified HTML
        body = soup.find("body")
        if body:
            return "".join(str(child) for child in body.children)

        return str(soup)

    def _clean_html(self, html: str) -> str:
        """Clean and normalize HTML content."""
        if not html:
            return ""

        soup = BeautifulSoup(html, "lxml")

        # The API returns content wrapped in <div id="sbo-rt-content">
        # Extract that if present
        content_div = soup.find("div", id="sbo-rt-content")
        if content_div:
            soup = BeautifulSoup(str(content_div), "lxml")

        # Remove script and style tags
        for tag in soup.find_all(["script", "style", "nav", "header", "footer", "meta"]):
            tag.decompose()

        # Remove O'Reilly specific elements and index terms
        for selector in [
            ".reader-nav",
            "[data-orm]",
            "[data-type='indexterm']",
            "a[data-type='indexterm']",
        ]:
            try:
                for tag in soup.select(selector):
                    tag.decompose()
            except Exception:
                pass

        # Make image URLs absolute (will be rewritten later after download)
        for img in soup.find_all("img"):
            src = img.get("src", "")
            if src and not src.startswith(("http", "data:")):
                img["src"] = urljoin(CONTENT_BASE, src)

        # Get inner content - either from body, the content div, or the whole thing
        body = soup.find("body")
        if body:
            return "".join(str(child) for child in body.children)

        # Return inner HTML without outer wrapper
        content_div = soup.find("div", id="sbo-rt-content")
        if content_div:
            return "".join(str(child) for child in content_div.children)

        return str(soup)

    def _extract_authors(self, chapters: list[Chapter]) -> list[str]:
        """Recover author names from the title page chapter.

        The epub API does not expose authors, but the title page contains a
        ``<p class="author">`` element with the author name(s).
        """
        for chapter in chapters:
            if not chapter.html_content:
                continue

            soup = BeautifulSoup(chapter.html_content, "lxml")
            author_el = soup.select_one("p.author, [class*='author']")
            if not author_el:
                continue

            text = author_el.get_text(" ", strip=True)
            if not text:
                continue

            # Split on common separators ("and", commas) into individual names.
            parts = re.split(r"\s+and\s+|,", text)
            authors = [p.strip() for p in parts if p.strip()]
            if authors:
                return authors

        return []

    def _find_cover_image(self, images: dict[str, Image]) -> bytes:
        """Find the cover image among already-downloaded chapter images."""
        for image in images.values():
            if "cover" in image.filename.lower():
                return image.data
        return b""

    def _fetch_cover(self, cover_url: str) -> bytes:
        """Fetch cover image."""
        if not cover_url:
            return b""

        try:
            # Make URL absolute if needed
            if not cover_url.startswith("http"):
                cover_url = urljoin(CONTENT_BASE, cover_url)

            response = self.http.get(cover_url)
            response.raise_for_status()
            return response.content
        except httpx.HTTPError as e:
            console.print(f"[yellow]Warning: Failed to fetch cover: {e}[/]")
            return b""

    def close(self) -> None:
        """Close the HTTP client."""
        self.http.close()

    def __enter__(self) -> "OreillyClient":
        return self

    def __exit__(self, *args) -> None:
        self.close()
