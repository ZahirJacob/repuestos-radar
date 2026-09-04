"""Login-token logic: pure, time-injectable, no Streamlit imports."""

from repuestos_radar.dashboard.auth import (
    TOKEN_TTL_SECONDS,
    check_password,
    make_token,
    token_valid,
)


def test_check_password_exact_match_only():
    assert check_password("clave", "clave")
    assert not check_password("clave ", "clave")
    assert not check_password("", "clave")


def test_token_roundtrip():
    token = make_token("clave", now=1_000_000.0)
    assert token_valid("clave", token, now=1_000_000.0)
    assert token_valid("clave", token, now=1_000_000.0 + TOKEN_TTL_SECONDS - 1)


def test_token_expires():
    token = make_token("clave", now=1_000_000.0)
    assert not token_valid("clave", token, now=1_000_000.0 + TOKEN_TTL_SECONDS + 1)


def test_token_bound_to_password():
    token = make_token("clave", now=1_000_000.0)
    assert not token_valid("otra", token, now=1_000_000.0)


def test_garbage_tokens_rejected():
    for garbage in ("", "no-dot", "123", "abc.def", "999999999999999999999999.x"):
        assert not token_valid("clave", garbage, now=1_000_000.0)


def test_tampered_expiry_rejected():
    token = make_token("clave", now=1_000_000.0)
    expires, signature = token.split(".", 1)
    tampered = f"{int(expires) + 999999}.{signature}"
    assert not token_valid("clave", tampered, now=1_000_000.0)


def test_signature_is_not_a_plain_hmac_of_the_password():
    """A stolen cookie must not be an offline password-cracking oracle: the
    signing key comes out of a slow KDF, never the raw password."""
    import hashlib
    import hmac

    token = make_token("clave", now=1_000_000.0)
    expires, signature = token.split(".", 1)
    naive = hmac.new(b"clave", expires.encode(), hashlib.sha256).hexdigest()
    assert signature != naive


def test_token_bound_to_cookie_secret():
    token = make_token("clave", now=1_000_000.0, secret="abc")
    assert token_valid("clave", token, now=1_000_000.0, secret="abc")
    assert not token_valid("clave", token, now=1_000_000.0, secret="xyz")
    assert not token_valid("clave", token, now=1_000_000.0)


def test_signing_key_derivation_is_deliberately_slow():
    from repuestos_radar.dashboard.auth import KDF_ITERATIONS, signing_key

    assert KDF_ITERATIONS >= 600_000
    assert signing_key("clave", "") != signing_key("clave", "s")
    assert signing_key("clave", "s") == signing_key("clave", "s")
    assert len(signing_key("clave", "s")) == 32


class _Clock:
    def __init__(self):
        self.now = 1000.0
        self.slept: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def _throttle(clock: _Clock):
    from repuestos_radar.dashboard.auth import LoginThrottle

    return LoginThrottle(now=lambda: clock.now, sleep=clock.sleep)


def test_throttle_gives_three_free_attempts_then_backs_off():
    clock = _Clock()
    throttle = _throttle(clock)
    for _ in range(3):
        throttle.wait()
        throttle.record(ok=False)
    assert clock.slept == [0, 0, 0]
    throttle.wait()  # 4th attempt
    throttle.record(ok=False)
    throttle.wait()  # 5th
    throttle.record(ok=False)
    throttle.wait()  # 6th
    assert clock.slept[3:] == [2, 4, 8]


def test_throttle_delay_is_capped():
    from repuestos_radar.dashboard.auth import MAX_LOGIN_DELAY_SECONDS

    clock = _Clock()
    throttle = _throttle(clock)
    for _ in range(12):
        throttle.wait()
        throttle.record(ok=False)
    assert max(clock.slept) == MAX_LOGIN_DELAY_SECONDS
    assert throttle.delay_seconds() == MAX_LOGIN_DELAY_SECONDS


def test_throttle_forgets_failures_older_than_the_window():
    from repuestos_radar.dashboard.auth import LOGIN_FAILURE_WINDOW_SECONDS

    clock = _Clock()
    throttle = _throttle(clock)
    for _ in range(5):
        throttle.wait()
        throttle.record(ok=False)
    assert throttle.delay_seconds() > 0
    clock.now += LOGIN_FAILURE_WINDOW_SECONDS + 1
    assert throttle.delay_seconds() == 0


def test_throttle_clears_on_a_successful_login():
    clock = _Clock()
    throttle = _throttle(clock)
    for _ in range(5):
        throttle.wait()
        throttle.record(ok=False)
    throttle.wait()
    throttle.record(ok=True)
    assert throttle.delay_seconds() == 0
