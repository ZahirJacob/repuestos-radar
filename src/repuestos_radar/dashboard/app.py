"""App shell: page config, login gate, navigation, freshness footer."""

import contextlib
import os

import streamlit as st

from repuestos_radar.dashboard import admin, auth, data, detail, home, radar, text_es
from repuestos_radar.report import format_day
from repuestos_radar.sources import CLOUD_CHANNELS, load_sources

_COOKIE_NAME = "repuestos_radar_session"


def _expected_password() -> str | None:
    # Streamlit secrets first (cloud), environment second (local/.env, tests).
    try:
        if "APP_PASSWORD" in st.secrets:
            return st.secrets["APP_PASSWORD"]
    except Exception:  # no secrets.toml configured — normal outside the cloud
        pass
    return os.environ.get("APP_PASSWORD")


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
        controller.set(_COOKIE_NAME, auth.make_token(password), max_age=auth.TOKEN_TTL_SECONDS)


def _count_reachable_sources(loader=load_sources) -> int | None:
    """Stores the radar can reach from at least one cloud channel (the daily
    run or the quick search); a store cloud_blocked on both is not counted.

    None when the registry cannot be read: a bad sources.yaml must never put
    a traceback on the login screen, only drop the count from the status line.
    """
    try:
        return sum(1 for source in loader() if source.blocked_channels != CLOUD_CHANNELS)
    except Exception:
        return None


@st.cache_data
def _radar_store_count() -> int | None:
    return _count_reachable_sources()


def login_status_line(count: int | None) -> str:
    """The mono line under the brand: singular for one store, plural otherwise
    (zero included), and just the place when the count is unknown."""
    if count is None:
        return text_es.LOGIN_STATUS_NO_COUNT
    if count == 1:
        return text_es.LOGIN_STATUS_ONE
    return text_es.LOGIN_STATUS.format(count=count)


def _require_login() -> None:
    password = _expected_password()
    if not password:
        st.error(text_es.NO_PASSWORD_CONFIGURED)
        st.stop()
    if st.session_state.get("authed"):
        return
    controller = _cookie_controller()
    token = _read_cookie(controller)
    if isinstance(token, str) and auth.token_valid(password, token):
        st.session_state["authed"] = True
        return
    radar.render_login_panel(login_status_line(_radar_store_count()))
    with st.form("login"):
        # "current-password": browsers offer save/autofill for an existing
        # password instead of Chrome's "create a strong password" sign-up prompt.
        entered = st.text_input(
            text_es.PASSWORD_LABEL, type="password", autocomplete="current-password"
        )
        submitted = st.form_submit_button(text_es.LOGIN_BUTTON, use_container_width=True)
    if submitted:
        if auth.check_password(entered, password):
            st.session_state["authed"] = True
            _write_cookie(controller, password)
            st.rerun()
        else:
            st.error(text_es.WRONG_PASSWORD)
    st.stop()


PAGES: dict[str, st.Page] = {}


def _build_pages() -> list[st.Page]:
    PAGES.clear()
    PAGES["home"] = st.Page(
        home.render, title=text_es.NAV_PRICES, icon="📱", url_path="home", default=True
    )
    PAGES["detail"] = st.Page(
        detail.render, title=text_es.NAV_DETAIL, icon="🔎", url_path="detalle"
    )
    PAGES["admin"] = st.Page(admin.render, title=text_es.NAV_SETTINGS, icon="🛠", url_path="ajustes")
    return list(PAGES.values())


def _freshness_footer() -> None:
    with data.open_session() as session:
        day = data.overall_latest_day(session)
    if day is None:
        st.caption(text_es.NO_DATA_AT_ALL)
    else:
        st.caption(f"{text_es.UPDATED_PREFIX} {format_day(day)}")


def main() -> None:
    st.set_page_config(page_title=text_es.APP_TITLE, page_icon="📡", layout="centered")
    _require_login()
    st.navigation(_build_pages()).run()
    _freshness_footer()
