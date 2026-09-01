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
