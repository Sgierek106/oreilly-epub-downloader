"""EPUB generation from O'Reilly book content."""

import re
from pathlib import Path

from ebooklib import epub
from rich.console import Console

from .models import Book, Chapter, Image

console = Console()


def create_epub(book: Book, output_path: Path) -> Path:
    """Create an EPUB file from book content.

    Args:
        book: Complete book with metadata and chapters
        output_path: Path to save the EPUB file

    Returns:
        Path to the created EPUB file
    """
    console.print(f"[bold]Creating EPUB: {book.metadata.title}[/]")

    # Create EPUB book
    epub_book = epub.EpubBook()

    # Set metadata
    epub_book.set_identifier(f"oreilly-{book.metadata.id}")
    epub_book.set_title(book.metadata.title)
    epub_book.set_language(book.metadata.language)

    for author in book.metadata.authors:
        epub_book.add_author(author)

    if book.metadata.publisher:
        epub_book.add_metadata("DC", "publisher", book.metadata.publisher)

    if book.metadata.description:
        epub_book.add_metadata("DC", "description", book.metadata.description)

    if book.metadata.isbn:
        epub_book.add_metadata("DC", "identifier", book.metadata.isbn, {"id": "isbn"})

    # Add cover image if available. Reuse the cover image already collected
    # from the chapters (if present) so the same bytes are not embedded twice.
    if book.cover_image:
        cover_filename = next(
            (
                img.filename
                for img in book.images.values()
                if img.data == book.cover_image
            ),
            None,
        )
        if cover_filename is None:
            cover_ext = _guess_image_extension(book.cover_image)
            cover_filename = f"cover.{cover_ext}"
        # create_page=False: the book's "Cover" chapter already shows the
        # image in the spine; we only need the image registered as the cover.
        epub_book.set_cover(cover_filename, book.cover_image, create_page=False)
        console.print("[dim]Added cover image[/]")

    # Add CSS
    css_content = _get_default_css()
    css_item = epub.EpubItem(
        uid="style",
        file_name="style/main.css",
        media_type="text/css",
        content=css_content.encode("utf-8"),
    )
    epub_book.add_item(css_item)

    # Add images (the cover is already added via set_cover)
    if book.images:
        added = 0
        for url, image in book.images.items():
            if not image.data:
                continue
            if book.cover_image and image.data == book.cover_image:
                continue
            img_item = epub.EpubItem(
                uid=f"img_{image.filename.replace('/', '_').replace('.', '_')}",
                file_name=image.filename,
                media_type=image.media_type,
                content=image.data,
            )
            epub_book.add_item(img_item)
            added += 1
        console.print(f"[dim]Added {added} images[/]")

    # Create chapters
    epub_chapters = []
    for chapter in book.chapters:
        if not chapter.html_content or not chapter.html_content.strip():
            console.print(f"[dim]Skipping empty chapter: {chapter.title}[/]")
            continue

        # Ensure chapter has actual body content
        if len(chapter.html_content.strip()) < 50:
            console.print(f"[dim]Skipping near-empty chapter: {chapter.title}[/]")
            continue

        epub_chapter = _create_chapter(chapter, css_item)
        epub_book.add_item(epub_chapter)
        epub_chapters.append(epub_chapter)

    if not epub_chapters:
        raise RuntimeError(
            "No chapters with content were found. "
            "This usually means authentication failed or book access is restricted."
        )

    console.print(f"[dim]Added {len(epub_chapters)} chapters[/]")

    # Create table of contents
    epub_book.toc = epub_chapters

    # Add navigation files
    epub_book.add_item(epub.EpubNcx())
    epub_book.add_item(epub.EpubNav())

    # Create spine (reading order)
    epub_book.spine = ["nav"] + epub_chapters

    # Ensure output directory exists
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Write EPUB file
    epub.write_epub(str(output_path), epub_book)
    console.print(f"[bold green]EPUB saved to: {output_path}[/]")

    return output_path


def _create_chapter(chapter: Chapter, css_item: epub.EpubItem) -> epub.EpubHtml:
    """Create an EPUB chapter from chapter content."""
    # Generate a safe filename
    safe_title = re.sub(r"[^\w\s-]", "", chapter.title)
    safe_title = re.sub(r"\s+", "_", safe_title)[:50]
    filename = f"ch{chapter.order:03d}_{safe_title}.xhtml"

    # Extract just the body content, stripping outer tags
    body_content = _extract_body_content(chapter.html_content)

    # Wrap content in proper XHTML structure
    content = _wrap_html_content(chapter.title, body_content)

    epub_chapter = epub.EpubHtml(
        title=chapter.title,
        file_name=filename,
        lang="en",
    )
    epub_chapter.content = content.encode("utf-8")
    epub_chapter.add_item(css_item)

    return epub_chapter


def _extract_body_content(html: str) -> str:
    """Extract inner body content from HTML, removing wrapper tags."""
    if not html:
        return ""

    # If it's wrapped in body tags, extract the inner content
    body_match = re.search(r"<body[^>]*>(.*)</body>", html, re.DOTALL | re.IGNORECASE)
    if body_match:
        return body_match.group(1).strip()

    # If it's a full HTML document, try to find body
    if "<html" in html.lower():
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "lxml")
        body = soup.find("body")
        if body:
            # Get inner HTML (children of body)
            return "".join(str(child) for child in body.children)

    return html


def _wrap_html_content(title: str, body_content: str) -> str:
    """Wrap HTML content in proper XHTML structure."""
    # Ensure we have some content
    if not body_content or not body_content.strip():
        body_content = "<p>No content available.</p>"

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
<head>
    <title>{_escape_html(title)}</title>
    <link rel="stylesheet" type="text/css" href="style/main.css"/>
</head>
<body>
    <h1>{_escape_html(title)}</h1>
    {body_content}
</body>
</html>"""


def _escape_html(text: str) -> str:
    """Escape HTML special characters."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _guess_image_extension(image_data: bytes) -> str:
    """Guess image extension from magic bytes."""
    if image_data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if image_data[:2] == b"\xff\xd8":
        return "jpg"
    if image_data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if image_data[:4] == b"RIFF" and image_data[8:12] == b"WEBP":
        return "webp"
    return "jpg"  # Default


def _get_default_css() -> str:
    """Get default CSS for the EPUB."""
    return """
/* Base styles */
body {
    font-family: Georgia, "Times New Roman", serif;
    font-size: 1em;
    line-height: 1.6;
    margin: 1em;
    padding: 0;
}

/* Headings */
h1, h2, h3, h4, h5, h6 {
    font-family: Helvetica, Arial, sans-serif;
    margin-top: 1.5em;
    margin-bottom: 0.5em;
    line-height: 1.2;
}

h1 { font-size: 1.8em; }
h2 { font-size: 1.5em; }
h3 { font-size: 1.3em; }
h4 { font-size: 1.1em; }

/* Paragraphs */
p {
    margin: 0.8em 0;
    text-align: justify;
}

/* Code */
pre, code {
    font-family: "Courier New", Courier, monospace;
    font-size: 0.9em;
    background-color: #f4f4f4;
}

pre {
    padding: 1em;
    overflow-x: auto;
    border: 1px solid #ddd;
    border-radius: 4px;
    white-space: pre-wrap;
    word-wrap: break-word;
}

code {
    padding: 0.2em 0.4em;
    border-radius: 3px;
}

pre code {
    padding: 0;
    background: none;
}

/* Lists */
ul, ol {
    margin: 0.8em 0;
    padding-left: 2em;
}

li {
    margin: 0.3em 0;
}

/* Images */
img {
    max-width: 100%;
    height: auto;
}

figure {
    margin: 1em 0;
    text-align: center;
}

figcaption {
    font-size: 0.9em;
    font-style: italic;
    color: #666;
}

/* Tables */
table {
    border-collapse: collapse;
    width: 100%;
    margin: 1em 0;
}

th, td {
    border: 1px solid #ddd;
    padding: 0.5em;
    text-align: left;
}

th {
    background-color: #f4f4f4;
    font-weight: bold;
}

/* Blockquotes */
blockquote {
    margin: 1em 0;
    padding: 0.5em 1em;
    border-left: 4px solid #ddd;
    font-style: italic;
    color: #555;
}

/* Links */
a {
    color: #0066cc;
    text-decoration: none;
}

a:hover {
    text-decoration: underline;
}

/* Notes and tips */
.note, .tip, .warning, .caution {
    padding: 1em;
    margin: 1em 0;
    border-radius: 4px;
}

.note {
    background-color: #e7f3ff;
    border-left: 4px solid #2196F3;
}

.tip {
    background-color: #e8f5e9;
    border-left: 4px solid #4CAF50;
}

.warning {
    background-color: #fff3e0;
    border-left: 4px solid #FF9800;
}

.caution {
    background-color: #ffebee;
    border-left: 4px solid #f44336;
}
"""
