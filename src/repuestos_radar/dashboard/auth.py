"""Shared-password auth with a signed remember-me token.

One password for the whole app (margins are business-sensitive). The cookie
holds "<expiry-unix>.<hmac>" — signed with the password itself, so changing
the password invalidates every outstanding token. Pure functions: the
Streamlit cookie glue lives in app.py.
"""

import hashlib
import hmac
import time

TOKEN_TTL_SECONDS = 30 * 24 * 3600  # ~30 days; the spec's "rarely re-asks"


def check_password(entered: str, expected: str) -> bool:
    """Constant-time comparison; never `==` on secrets."""
    return hmac.compare_digest(entered.encode(), expected.encode())


def _sign(password: str, message: str) -> str:
    return hmac.new(password.encode(), message.encode(), hashlib.sha256).hexdigest()


def make_token(password: str, now: float | None = None) -> str:
    expires = int((time.time() if now is None else now) + TOKEN_TTL_SECONDS)
    return f"{expires}.{_sign(password, str(expires))}"


def token_valid(password: str, token: str, now: float | None = None) -> bool:
    expires_text, _, signature = token.partition(".")
    if not signature:
        return False
    try:
        expires = int(expires_text)
    except ValueError:
        return False
    if (time.time() if now is None else now) > expires:
        return False
    return hmac.compare_digest(_sign(password, expires_text), signature)
