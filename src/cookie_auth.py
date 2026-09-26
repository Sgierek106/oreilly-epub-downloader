"""Cookie-based authentication."""

import base64
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


class CookieExpiredError(Exception):
    """Raised when the O'Reilly session cookie has expired."""


@dataclass
class Session:
    """Authenticated session."""

    cookies: dict[str, str]

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

    return Session(cookies=cookies)


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
