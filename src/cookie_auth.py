"""Cookie-based authentication."""

import base64
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


class CookieExpiredError(Exception):
    """Raised when the O'Reilly session cookie has expired."""


class BrowserCookieError(Exception):
    """Raised when O'Reilly cookies cannot be read from the local browser."""


#: Cookie domains that carry the O'Reilly session.
#:
#: Direct logins store the JWT under ``.oreilly.com``. Institutional access
#: through the OCLC IdM proxy (OpenAthens-style, e.g.
#: ``learning-oreilly-com.<library>.idm.oclc.org``) stores the same
#: ``orm-jwt``/``orm-rt`` under the proxy's own domain instead — the JWT
#: still authenticates against learning.oreilly.com directly, so we just
#: need to harvest it from there too.
COOKIE_DOMAINS = (".oreilly.com", ".idm.oclc.org")

#: Browsers ``browser_cookie3`` can read on this platform.
SUPPORTED_BROWSERS = (
    "chrome",
    "chromium",
    "brave",
    "edge",
    "opera",
    "firefox",
    "librewolf",
    "vivaldi",
    "arc",
)


@dataclass
class Session:
    """Authenticated session."""

    cookies: dict[str, str]
    source: str = "unknown"

    def get_cookie_header(self) -> str:
        """Format cookies for HTTP header."""
        return "; ".join(f"{k}={v}" for k, v in self.cookies.items())


def load_cookies(cookie_file: Path) -> Session:
    """Load session from a JSON cookie file."""
    data = json.loads(cookie_file.read_text())

    if isinstance(data, dict):
        cookies = data
    elif isinstance(data, list):
        cookies = {c["name"]: c["value"] for c in data if "name" in c}
    else:
        raise ValueError("Invalid cookie file format")

    if "orm-jwt" not in cookies:
        raise ValueError("Missing orm-jwt cookie - are you logged into O'Reilly?")

    return Session(cookies=cookies, source=str(cookie_file))


def load_cookies_from_browser(browser: str | None = None) -> Session:
    """Load O'Reilly session cookies straight from a local browser.

    Reads the browser's own (encrypted) cookie store instead of a
    hand-exported ``cookies.json``, so no DevTools copy/paste is needed.
    The browser must already be logged into https://learning.oreilly.com —
    while a logged-in tab is open the site keeps the session token fresh,
    which is what makes long batch runs self-healing.

    On Linux, Chromium-based browsers encrypt their cookies with a key in
    the Secret Service keyring (gnome-keyring/kwallet); the first read may
    show a one-time unlock/allow prompt. Firefox stores cookies unencrypted
    unless a primary password is set.

    Args:
        browser: Browser name (one of :data:`SUPPORTED_BROWSERS`). ``None``
            tries every installed browser and merges the results.

    Raises:
        BrowserCookieError: If the browser store cannot be read, or holds
            no ``orm-jwt`` cookie (i.e. you are not logged in there).
    """
    try:
        import browser_cookie3
    except ImportError as e:  # pragma: no cover - dependency is declared
        raise BrowserCookieError(
            "browser_cookie3 is not installed. Run: pip install browser-cookie3"
        ) from e

    def read(name: str) -> dict[str, str]:
        """Pull O'Reilly session cookies from one browser as a plain dict.

        Reads every domain in :data:`COOKIE_DOMAINS` (direct and proxy) and
        merges them. ``browser_cookie3`` raises a menagerie of error types
        for missing browsers, locked databases and keyring problems (e.g.
        a bare ``TypeError`` from its macOS-only Arc loader on Linux), so
        we catch broadly and let the caller decide what to report.
        """
        loader = getattr(browser_cookie3, name)
        cookies: dict[str, str] = {}
        for domain in COOKIE_DOMAINS:
            try:
                jar = loader(domain_name=domain)
            except Exception:
                continue  # domain not present in this browser — try the next
            cookies.update({c.name: c.value for c in jar if c.value})
        return cookies

    if browser:
        name = browser.lower()
        if name not in SUPPORTED_BROWSERS:
            raise BrowserCookieError(
                f"Unsupported browser {browser!r}. "
                f"Supported: {', '.join(SUPPORTED_BROWSERS)}"
            )
        try:
            cookies = read(name)
        except Exception as e:
            raise BrowserCookieError(f"Could not read {name} cookies: {e}") from e
        source = f"browser:{name}"
    else:
        cookies = {}
        for name in SUPPORTED_BROWSERS:
            try:
                cookies.update(read(name))
            except Exception:
                continue  # browser not installed / unreadable — try the next
        source = "browser:auto"

    if "orm-jwt" not in cookies:
        raise BrowserCookieError(
            "No orm-jwt cookie found in the browser. "
            "Log into https://learning.oreilly.com there first."
        )

    return Session(cookies=cookies, source=source)


def check_cookie_expiry(session: Session) -> None:
    """Verify the orm-jwt cookie has not expired.

    The orm-jwt cookie is a JWT whose payload carries an ``exp`` (expiry)
    claim. When the cookie is expired, the O'Reilly API silently returns
    truncated preview content instead of full chapters, so we check the
    expiry up front and fail fast with a clear message.

    Raises:
        CookieExpiredError: If the cookie is expired or missing an expiry.
    """
    token = session.cookies.get("orm-jwt", "")
    parts = token.split(".")
    if len(parts) < 2:
        raise CookieExpiredError(
            "orm-jwt cookie is malformed (not a JWT). "
            "Please refresh cookies.json."
        )

    try:
        payload = parts[1]
        payload += "=" * (-len(payload) % 4)  # restore base64 padding
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except (ValueError, json.JSONDecodeError) as e:
        raise CookieExpiredError(
            f"Could not decode orm-jwt cookie ({e}). "
            "Please refresh cookies.json."
        ) from e

    exp = claims.get("exp")
    if not isinstance(exp, (int, float)):
        raise CookieExpiredError(
            "orm-jwt cookie has no expiry claim. "
            "Please refresh cookies.json."
        )

    if datetime.now(timezone.utc) >= datetime.fromtimestamp(exp, tz=timezone.utc):
        expired_at = datetime.fromtimestamp(exp, tz=timezone.utc)
        raise CookieExpiredError(
            f"Cookies expired at {expired_at:%Y-%m-%d %H:%M:%S} UTC. "
            "Please refresh cookies.json."
        )
