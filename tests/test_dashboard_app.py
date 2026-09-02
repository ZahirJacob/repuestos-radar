"""App-shell tests: login gate and page navigation via streamlit AppTest."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from streamlit.proto.TextInput_pb2 import TextInput as TextInputProto
from streamlit.testing.v1 import AppTest

from repuestos_radar.dashboard import data
from repuestos_radar.db import get_engine, get_session_factory, init_db
from repuestos_radar.models import Listing, ServicePrice, TrackedItem

_ENTRY_SCRIPT = Path(__file__).resolve().parent.parent / "streamlit_app.py"


@pytest.fixture()
def seeded_db(tmp_path, monkeypatch):
    # cached_engine() is @st.cache_resource'd (no args) so its cache is
    # process-global, not per-AppTest-run — without clearing it here, a test
    # that logs in reuses whatever engine an earlier test cached, pointed at
    # that earlier test's tmp_path database instead of this one's.
    data.cached_engine.clear()
    url = f"sqlite:///{tmp_path}/dash.db"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("APP_PASSWORD", "clave-test")
    engine = get_engine(url)
    init_db(engine)
    with get_session_factory(engine)() as session:
        item = TrackedItem(query="modulo a32")
        session.add(item)
        session.flush()
        session.add(
            Listing(
                tracked_item_id=item.id,
                source_slug="celuphone",
                external_id="1",
                title="Modulo Samsung A32 incell",
                price=Decimal("20700"),
                currency="ARS",
                condition="unknown",
                url="https://celuphone.com.ar/p/1",
                fetched_date=date(2026, 9, 1),
                relevance="match",
                relevance_score=0.9,
            )
        )
        session.add(
            ServicePrice(
                tracked_item_id=item.id, label="Cambio módulo A32", price_ars=Decimal("85000")
            )
        )
        session.commit()
    return url


def _app(seeded_db) -> AppTest:
    at = AppTest.from_file(str(_ENTRY_SCRIPT), default_timeout=10)
    return at


def _body(at: AppTest) -> str:
    """All on-screen text as one string.

    Installed-Streamlit note: str(element) doesn't render text in this
    version (it prints the element's repr, e.g. "Markdown()") — use
    ``.value`` instead, and pull from every element kind that carries
    visible text, not just markdown (e.g. st.subheader is its own kind).
    """
    parts = []
    for group in (at.title, at.subheader, at.markdown, at.caption):
        parts.extend(str(element.value) for element in group)
    return " ".join(parts)


def test_login_gate_blocks_without_password(seeded_db):
    at = _app(seeded_db).run()
    assert at.text_input  # the password field is shown
    assert "authed" not in at.session_state or not at.session_state["authed"]


def test_password_field_is_marked_as_an_existing_password(seeded_db):
    """autocomplete="current-password": browsers offer to save/autofill the
    password instead of Chrome's "create a strong password" sign-up prompt."""
    at = _app(seeded_db).run()
    field = at.text_input[0]
    assert field.autocomplete == "current-password"
    assert field.proto.type == TextInputProto.PASSWORD  # still masked, of course


def test_wrong_password_rejected(seeded_db):
    at = _app(seeded_db).run()
    at.text_input[0].set_value("wrong").run()
    at.button[0].set_value(True).run()
    assert "authed" not in at.session_state or not at.session_state["authed"]


def test_right_password_enters_and_home_lists_items(seeded_db):
    at = _app(seeded_db).run()
    at.text_input[0].set_value("clave-test").run()
    at.button[0].set_value(True).run()
    assert at.session_state["authed"] is True
    body = _body(at)
    assert "modulo a32" in body.lower()
    assert "$20.700" in body


def test_missing_app_password_is_a_visible_config_error(seeded_db, monkeypatch):
    monkeypatch.delenv("APP_PASSWORD")
    at = _app(seeded_db).run()
    assert at.error


class _RaisingCookieController:
    """Stub standing in for a cookie component whose JS bridge misbehaves."""

    def get(self, name):
        raise RuntimeError("cookie backend exploded")

    def set(self, *args, **kwargs):
        raise RuntimeError("cookie backend exploded")


def test_cookie_component_failure_degrades_gracefully(seeded_db, monkeypatch):
    """A raising cookie READ must never crash the script, and must never log
    anyone in (fail-secure); a raising cookie WRITE on successful login must
    not stop the login from succeeding."""
    from repuestos_radar.dashboard import app as dashboard_app

    monkeypatch.setattr(dashboard_app, "_cookie_controller", lambda: _RaisingCookieController())

    at = _app(seeded_db).run()
    assert not at.exception
    assert at.text_input  # still reaches the password form, not a crash
    assert "authed" not in at.session_state or not at.session_state["authed"]

    at.text_input[0].set_value("clave-test").run()
    at.button[0].set_value(True).run()
    assert not at.exception
    assert at.session_state["authed"] is True  # a write failure doesn't block login


def _login(at):
    at.text_input[0].set_value("clave-test").run()
    at.button[0].set_value(True).run()
    return at


def test_home_card_shows_margin_and_no_warning_for_clean_data(seeded_db):
    at = _login(_app(seeded_db).run())
    body = _body(at)
    assert "$64.300" in body  # 85000 - 20700
    assert "revisar" not in body


def test_home_card_says_no_data_today_for_empty_item(seeded_db, monkeypatch):
    engine = get_engine(seeded_db)
    with get_session_factory(engine)() as session:
        session.add(TrackedItem(query="bateria iphone 11"))
        session.commit()
    at = _login(_app(seeded_db).run())
    body = _body(at)
    assert "sin datos de hoy" in body
