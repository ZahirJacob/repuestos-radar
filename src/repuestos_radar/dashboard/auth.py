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
    once per browser session. The cache is keyed by the (password, secret)
    pair — the configured password, which already lives in the process
    environment, never a password someone typed — so it only ever holds the
    one or two pairs a process sees.
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
    ``LOGIN_FAILURE_WINDOW_SECONDS``, every further WRONG attempt waits
    2, 4, 8 … seconds, capped at ``MAX_LOGIN_DELAY_SECONDS``. The wait holds
    the throttle's lock, so parallel sessions queue behind it instead of
    guessing side by side: the aggregate guess rate is bounded no matter
    how many sessions a guesser opens. The caller checks the password
    FIRST and only calls :meth:`wait` on a wrong one, so the shop typing
    the right password never queues behind a guesser (a correct answer is
    revealed by the response anyway; there is no timing oracle to hide),
    and devices with a valid cookie never touch the throttle at all. A
    delay (never a lockout) keeps a guesser from locking the shop out of its
    own dashboard; a correct password clears the backlog. ``now`` and
    ``sleep`` are injectable for tests.
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

    @staticmethod
    def _delay_for(recent_failures: int) -> float:
        """0 while the free attempts last, then 2, 4, 8 … capped."""
        over = recent_failures - LOGIN_FREE_FAILURES
        if over < 0:
            return 0.0
        return min(float(2 ** (over + 1)), MAX_LOGIN_DELAY_SECONDS)

    def _prune(self) -> None:
        """Caller holds the lock."""
        cutoff = self._now() - LOGIN_FAILURE_WINDOW_SECONDS
        while self._failures and self._failures[0] < cutoff:
            self._failures.popleft()

    def delay_seconds(self) -> float:
        """Seconds the next wrong attempt would wait, given the recent failures.

        Lock-free on purpose (a snapshot, no pruning): it is read while
        rendering the login error, and must not queue behind a sleeper.
        """
        cutoff = self._now() - LOGIN_FAILURE_WINDOW_SECONDS
        recent = sum(1 for stamp in tuple(self._failures) if stamp >= cutoff)
        return self._delay_for(recent)

    def wait(self) -> None:
        """Block for the current delay; call after a WRONG password, before
        recording it (see the class docstring for why not before every check)."""
        with self._lock:
            self._prune()
            self._sleep(self._delay_for(len(self._failures)))

    def record(self, *, ok: bool) -> None:
        with self._lock:
            if ok:
                self._failures.clear()
            else:
                self._prune()
                self._failures.append(self._now())
