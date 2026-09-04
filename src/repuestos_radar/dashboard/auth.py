"""Shared-password auth: a signed remember-me token and a login throttle.

One password for the whole app (margins are business-sensitive). The cookie
holds "<expiry-unix>.<hmac>". The HMAC key is NOT the password itself: it is
derived from the password and an optional server-side secret through a slow
KDF, so a copied cookie (shared phone, synced browser) is not an offline
password-cracking oracle — and with ``APP_COOKIE_SECRET`` set, not even a
slow one, since the attacker lacks the secret. Changing either the password
or the secret still invalidates every outstanding token.

Pure functions and one small stateful class; the Streamlit glue lives in
app.py.
"""

import hashlib
import hmac
import threading
import time
from collections import deque
from collections.abc import Callable
from functools import lru_cache

TOKEN_TTL_SECONDS = 30 * 24 * 3600  # ~30 days; the spec's "rarely re-asks"

KDF_ITERATIONS = 600_000
"""PBKDF2-HMAC-SHA256 rounds (OWASP's 2023 floor). ~0.3 s once per process
per password, cached; never on the request path after that."""
_KDF_SALT_PREFIX = b"repuestos-radar remember-me v2:"

LOGIN_FREE_FAILURES = 3
"""Wrong passwords in the window that cost no delay (typos are free)."""
LOGIN_FAILURE_WINDOW_SECONDS = 10 * 60
MAX_LOGIN_DELAY_SECONDS = 30.0


def check_password(entered: str, expected: str) -> bool:
    """Constant-time comparison; never `==` on secrets."""
    return hmac.compare_digest(entered.encode(), expected.encode())


@lru_cache(maxsize=8)
def signing_key(password: str, secret: str = "") -> bytes:
    """The token-signing key for this password and cookie secret.

    Cached: the KDF is deliberately expensive, and the app validates a cookie
    once per browser session. The cache holds the derived key, not the
    password, and only ever the handful of (password, secret) pairs a
    process sees.
    """
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode(), _KDF_SALT_PREFIX + secret.encode(), KDF_ITERATIONS
    )


def _sign(password: str, message: str, secret: str) -> str:
    return hmac.new(signing_key(password, secret), message.encode(), hashlib.sha256).hexdigest()


def make_token(password: str, now: float | None = None, *, secret: str = "") -> str:
    expires = int((time.time() if now is None else now) + TOKEN_TTL_SECONDS)
    return f"{expires}.{_sign(password, str(expires), secret)}"


def token_valid(password: str, token: str, now: float | None = None, *, secret: str = "") -> bool:
    expires_text, _, signature = token.partition(".")
    if not signature:
        return False
    try:
        expires = int(expires_text)
    except ValueError:
        return False
    if (time.time() if now is None else now) > expires:
        return False
    return hmac.compare_digest(_sign(password, expires_text, secret), signature)


class LoginThrottle:
    """Process-wide brake on password guessing.

    Streamlit has no login rate limit of its own and a guesser can open a
    fresh session per attempt, so the count is global, not per session:
    after ``LOGIN_FREE_FAILURES`` wrong passwords in the last
    ``LOGIN_FAILURE_WINDOW_SECONDS``, every further attempt first waits
    2, 4, 8 … seconds, capped at ``MAX_LOGIN_DELAY_SECONDS``. The wait holds
    the throttle's lock, so parallel sessions queue behind it instead of
    guessing side by side. A delay (never a lockout) keeps a guesser from
    locking the shop out of its own dashboard; a correct password clears
    the backlog. ``now`` and ``sleep`` are injectable for tests.
    """

    def __init__(
        self,
        *,
        now: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], object] = time.sleep,
    ) -> None:
        self._now = now
        self._sleep = sleep
        self._failures: deque[float] = deque()
        self._lock = threading.Lock()

    def _current_delay(self) -> float:
        """Caller holds the lock. 0 while the free attempts last, then 2, 4, 8 …"""
        cutoff = self._now() - LOGIN_FAILURE_WINDOW_SECONDS
        while self._failures and self._failures[0] < cutoff:
            self._failures.popleft()
        over = len(self._failures) - LOGIN_FREE_FAILURES
        if over < 0:
            return 0.0
        return min(float(2 ** (over + 1)), MAX_LOGIN_DELAY_SECONDS)

    def delay_seconds(self) -> float:
        """Seconds the next attempt has to wait, given the recent failures."""
        with self._lock:
            return self._current_delay()

    def wait(self) -> None:
        """Block for the current delay; call right before checking a password."""
        with self._lock:
            self._sleep(self._current_delay())

    def record(self, *, ok: bool) -> None:
        with self._lock:
            if ok:
                self._failures.clear()
            else:
                self._failures.append(self._now())
