"""Demo mode through the app shell: no login, banner, ES/EN, read-only settings."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from repuestos_radar.dashboard import data, demo, text_en, text_es

_ROOT = Path(__file__).resolve().parent.parent
_ENTRY = _ROOT / "streamlit_app.py"
_DEMO_ENTRY = _ROOT / "demo_app.py"
_ADMIN_SCRIPT = "from repuestos_radar.dashboard import admin\nadmin.render()\n"
_ORIGIN_SCRIPT = "from repuestos_radar.dashboard import detail\ndetail._reference_point()\n"


@pytest.fixture
def demo_env(tmp_path, monkeypatch):
    data.cached_engine.clear()
    monkeypatch.setattr(demo, "_db_dir", tmp_path)
    monkeypatch.setenv(demo.DEMO_ENV, "1")
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://must-not-be-used.invalid/db")


def _body(at: AppTest) -> str:
    parts = []
    for group in (at.title, at.subheader, at.markdown, at.caption, at.info):
        parts.extend(str(element.value) for element in group)
    return " ".join(parts)


def test_demo_needs_no_password_and_shows_the_banner_and_sample_prices(demo_env):
    at = AppTest.from_file(str(_ENTRY), default_timeout=15).run()
    assert not at.exception
    assert not at.text_input  # no password field
    assert at.info and text_es.DEMO_BANNER in at.info[0].value
    body = _body(at)
    for query, _, _, _ in demo._ITEMS:
        assert query in body
    assert "Mejor precio en" in body and "$" in body
    assert "<h1>Precios</h1>" in body


def test_demo_entry_script_turns_the_mode_on_by_itself(tmp_path, monkeypatch):
    data.cached_engine.clear()
    monkeypatch.setattr(demo, "_db_dir", tmp_path)
    monkeypatch.setenv(demo.DEMO_ENV, "")  # so monkeypatch restores the absence afterwards
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    at = AppTest.from_file(str(_DEMO_ENTRY), default_timeout=15).run()
    assert not at.exception
    assert not at.text_input
    assert at.info


def test_language_toggle_switches_every_string(demo_env):
    at = AppTest.from_file(str(_ENTRY), default_timeout=15).run()
    assert "lang" not in at.session_state  # Spanish by default, nothing stored yet
    at.segmented_control[0].set_value("en").run()
    assert not at.exception
    assert at.session_state["lang"] == "en"
    body = _body(at)
    assert text_en.DEMO_BANNER in at.info[0].value
    assert "<h1>Prices</h1>" in body and "Best price at" in body
    assert "Mejor precio" not in body


def test_lang_query_param_picks_english_for_a_shared_link(demo_env):
    at = AppTest.from_file(str(_ENTRY), default_timeout=15)
    at.query_params["lang"] = "en"
    at.run()
    assert not at.exception
    assert at.session_state["lang"] == "en"
    assert "<h1>Prices</h1>" in _body(at)


def test_demo_settings_page_is_read_only(demo_env):
    at = AppTest.from_string(_ADMIN_SCRIPT, default_timeout=15).run()
    assert not at.exception
    body = _body(at)
    assert text_es.DEMO_ADMIN_READ_ONLY in body
    assert text_es.DEMO_QUICK_SEARCH_OFF in body
    assert "Cambio módulo A32" in body and "modulo samsung a32" in body
    assert not at.button and not at.text_input and not at.selectbox and not at.expander


def test_demo_measures_from_central_rosario_and_never_names_the_client(demo_env, monkeypatch):
    monkeypatch.setenv("SHOP_LAT", demo.DEMO_SHOP_LAT)
    monkeypatch.setenv("SHOP_LON", demo.DEMO_SHOP_LON)
    at = AppTest.from_string(_ORIGIN_SCRIPT, default_timeout=15).run()
    assert not at.exception
    assert [m.value for m in at.markdown] == [f"📍 {text_es.DEMO_FROM_SHOP}"]
    assert at.button[1].label == text_es.DEMO_BACK_TO_SHOP
    at.session_state["geo_denied"] = True
    at.run()
    assert [c.value for c in at.caption] == [text_es.DEMO_LOCATION_DENIED]
    screen = " ".join(m.value for m in at.markdown) + " ".join(c.value for c in at.caption)
    assert "Activcelu" not in screen


def test_client_app_keeps_login_and_ignores_the_language_switch(tmp_path, monkeypatch):
    """The bypass and the toggle exist only behind the demo flag."""
    data.cached_engine.clear()
    monkeypatch.delenv(demo.DEMO_ENV, raising=False)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/client.db")
    monkeypatch.setenv("APP_PASSWORD", "clave-test")
    at = AppTest.from_file(str(_ENTRY), default_timeout=15)
    at.query_params["lang"] = "en"
    at.run()
    assert not at.exception
    assert at.text_input and at.text_input[0].label == text_es.PASSWORD_LABEL
    assert not at.segmented_control and not at.info
    assert "lang" not in at.session_state
