"""App shell: page config, login gate, navigation, freshness footer."""

import contextlib
import os

import streamlit as st

from repuestos_radar.dashboard import admin, auth, demo, detail, home, radar, text
from repuestos_radar.dashboard.text import t
from repuestos_radar.sources import CLOUD_CHANNELS, load_sources

_COOKIE_NAME = "repuestos_radar_session"

# One throttle per server process: the guessing brake has to see every
# session's failed attempts, not just the current one's.
_THROTTLE = auth.LoginThrottle()


def _setting(name: str) -> str | None:
    # Streamlit secrets first (cloud), environment second (local/.env, tests).
    try:
        if name in st.secrets:
            return st.secrets[name]
    except Exception:  # no secrets.toml configured — normal outside the cloud
        pass
    return os.environ.get(name)


def _expected_password() -> str | None:
    return _setting("APP_PASSWORD")


def _cookie_secret() -> str:
    """Server-side half of the remember-me signing key (see auth.py).

    Optional: without it the token is still signed with a slow KDF of the
    password; with it a copied cookie cannot be cracked at all.
    """
    return _setting("APP_COOKIE_SECRET") or ""


def _cookie_controller():
    """The cookie component, or None when unavailable (AppTest, import failure)."""
    try:
        from streamlit_cookies_controller import CookieController

        return CookieController()
    except Exception:
        return None


def _read_cookie(controller) -> str | None:
    """The remembered token, or None on missing cookie / component failure.

    A cookie-component read failure must degrade to session-only login, never
    crash the script and never count as authentication — so any exception
    here is treated exactly like "no cookie."
    """
    if not controller:
        return None
    try:
        return controller.get(_COOKIE_NAME)
    except Exception:
        return None


def _write_cookie(controller, password: str) -> None:
    """Best-effort remember-me write; a failure here must not break login."""
    if not controller:
        return
    with contextlib.suppress(Exception):
        # secure=True: never over plain HTTP (localhost counts as secure in
        # browsers, so local runs keep working). The component's default
        # same_site="strict" stands. HttpOnly is out of reach — the component
        # writes the cookie from JavaScript — which is why the token itself
        # must be worthless offline (see auth.py).
        controller.set(
            _COOKIE_NAME,
            auth.make_token(password, secret=_cookie_secret()),
            max_age=auth.TOKEN_TTL_SECONDS,
            secure=True,
        )


def _clear_cookie(controller) -> None:
    if not controller:
        return
    with contextlib.suppress(Exception):
        controller.remove(_COOKIE_NAME)


def _logout(controller) -> None:
    st.session_state.pop("authed", None)
    # The component removes the cookie from JavaScript, asynchronously; on
    # the very next rerun the old value can still be read back. This flag
    # keeps the session on the login form until a password is typed again.
    st.session_state["logged_out"] = True
    _clear_cookie(controller)
    st.rerun()


def _count_reachable_sources(loader=load_sources) -> int | None:
    """Stores the radar can reach from at least one cloud channel (the daily
    run or the quick search); a store cloud_blocked on both is not counted.

    None when the registry cannot be read: a bad sources.yaml must never put
    a traceback on the login screen, only drop the count from the status line.
    """
    try:
        return sum(
            1
            for source in loader()
            if not all(source.is_blocked(channel) for channel in CLOUD_CHANNELS)
        )
    except Exception:
        return None


@st.cache_data
def _radar_store_count() -> int | None:
    return _count_reachable_sources()


def login_status_line(count: int | None) -> str:
    """The mono line under the brand: singular for one store, plural otherwise
    (zero included), and just the place when the count is unknown."""
    if count is None:
        return t.LOGIN_STATUS_NO_COUNT
    if count == 1:
        return t.LOGIN_STATUS_ONE
    return t.LOGIN_STATUS.format(count=count)


def _require_login() -> None:
    if demo.is_demo():
        return
    password = _expected_password()
    if not password:
        st.error(t.NO_PASSWORD_CONFIGURED)
        st.stop()
    if st.session_state.get("authed"):
        return
    controller = _cookie_controller()
    token = None if st.session_state.get("logged_out") else _read_cookie(controller)
    if isinstance(token, str) and auth.token_valid(password, token, secret=_cookie_secret()):
        st.session_state["authed"] = True
        return
    radar.render_login_panel(login_status_line(_radar_store_count()))
    with st.form("login"):
        # "current-password": browsers offer save/autofill for an existing
        # password instead of Chrome's "create a strong password" sign-up prompt.
        entered = st.text_input(t.PASSWORD_LABEL, type="password", autocomplete="current-password")
        submitted = st.form_submit_button(t.LOGIN_BUTTON, use_container_width=True)
    if submitted:
        # Check first, wait only on a wrong password: the shop typing the
        # right one never queues behind a guesser (see auth.LoginThrottle).
        if auth.check_password(entered, password):
            _THROTTLE.record(ok=True)
            st.session_state["authed"] = True
            st.session_state.pop("logged_out", None)
            _write_cookie(controller, password)
            st.rerun()
        _THROTTLE.wait()
        _THROTTLE.record(ok=False)
        st.error(t.WRONG_PASSWORD)
        if _THROTTLE.delay_seconds() > 0:
            st.error(t.LOGIN_THROTTLED)
    st.stop()


PAGES: dict[str, st.Page] = {}


def _build_pages() -> list[st.Page]:
    PAGES.clear()
    PAGES["home"] = st.Page(
        home.render, title=t.NAV_PRICES, icon="📱", url_path="home", default=True
    )
    PAGES["detail"] = st.Page(detail.render, title=t.NAV_DETAIL, icon="🔎", url_path="detalle")
    PAGES["admin"] = st.Page(admin.render, title=t.NAV_SETTINGS, icon="🛠", url_path="ajustes")
    return list(PAGES.values())


_LANGUAGE_LABELS = {"es": "ES", "en": "EN"}


def _adopt_language_from_url() -> None:
    """``?lang=en`` picks the language for a shared demo link, once per session."""
    if st.session_state.get("lang-from-url"):
        return
    st.session_state["lang-from-url"] = True
    wanted = st.query_params.get("lang")
    if wanted in text.LANGUAGES:
        text.set_language(wanted)


def _render_demo_banner() -> None:
    """The sample-data notice and the ES/EN toggle, above every demo page."""
    _adopt_language_from_url()
    notice, toggle = st.columns([4, 1], vertical_alignment="center")
    choice = toggle.segmented_control(
        t.DEMO_LANGUAGE_LABEL,
        list(_LANGUAGE_LABELS),
        format_func=_LANGUAGE_LABELS.get,
        default=text.current_language(),
        selection_mode="single",
        label_visibility="collapsed",
        key="lang-toggle",
    )
    if choice in text.LANGUAGES and choice != text.current_language():
        text.set_language(choice)
        st.rerun()
    notice.info(t.DEMO_BANNER)


def main() -> None:
    st.set_page_config(page_title=t.APP_TITLE, page_icon="📡", layout="centered")
    _require_login()
    if demo.is_demo():
        _render_demo_banner()
    st.navigation(_build_pages()).run()
    if not demo.is_demo() and st.sidebar.button(t.LOGOUT_BUTTON, use_container_width=True):
        _logout(_cookie_controller())
