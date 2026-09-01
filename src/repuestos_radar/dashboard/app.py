"""App shell: page config, login gate, navigation, freshness footer."""

import os

import streamlit as st

from repuestos_radar.dashboard import admin, auth, data, detail, home, text_es
from repuestos_radar.report import format_day

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


def _require_login() -> None:
    password = _expected_password()
    if not password:
        st.error(text_es.NO_PASSWORD_CONFIGURED)
        st.stop()
    if st.session_state.get("authed"):
        return
    controller = _cookie_controller()
    token = controller.get(_COOKIE_NAME) if controller else None
    if isinstance(token, str) and auth.token_valid(password, token):
        st.session_state["authed"] = True
        return
    st.title(text_es.APP_TITLE)
    with st.form("login"):
        entered = st.text_input(text_es.PASSWORD_LABEL, type="password")
        submitted = st.form_submit_button(text_es.LOGIN_BUTTON, use_container_width=True)
    if submitted:
        if auth.check_password(entered, password):
            st.session_state["authed"] = True
            if controller:
                controller.set(
                    _COOKIE_NAME, auth.make_token(password), max_age=auth.TOKEN_TTL_SECONDS
                )
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
    st.set_page_config(page_title=text_es.APP_TITLE, page_icon="📱", layout="centered")
    _require_login()
    st.navigation(_build_pages()).run()
    _freshness_footer()
