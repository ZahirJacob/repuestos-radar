"""App-shell tests: login gate and page navigation via streamlit AppTest."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from streamlit.proto.TextInput_pb2 import TextInput as TextInputProto
from streamlit.testing.v1 import AppTest

from repuestos_radar.dashboard import app as dashboard_app
from repuestos_radar.dashboard import data
from repuestos_radar.db import get_engine, get_session_factory, init_db
from repuestos_radar.models import Listing, ServicePrice, TrackedItem
from repuestos_radar.sources import CLOUD_CHANNELS, Source, load_sources

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


def test_login_screen_shows_the_radar_panel_instead_of_a_title(seeded_db):
    """The brand now lives in the radar panel (raw HTML through st.markdown),
    so there is no st.title on the login screen any more."""
    at = _app(seeded_db).run()
    assert not at.title
    panels = [m.value for m in at.markdown if "<svg" in m.value]
    assert len(panels) == 1
    assert "Repuestos Radar" in panels[0]
    reachable = sum(1 for source in load_sources() if source.blocked_channels != CLOUD_CHANNELS)
    assert reachable > 1  # the plural line is the one on screen today
    assert f"Desde Rosario · {reachable} tiendas en el radar" in panels[0]


def test_login_status_line_picks_singular_plural_or_no_count():
    assert dashboard_app.login_status_line(6) == "Desde Rosario · 6 tiendas en el radar"
    assert dashboard_app.login_status_line(1) == "Desde Rosario · 1 tienda en el radar"
    assert dashboard_app.login_status_line(0) == "Desde Rosario · 0 tiendas en el radar"
    assert dashboard_app.login_status_line(None) == "Desde Rosario"


def test_reachable_source_count_fails_soft_on_a_bad_registry():
    def broken_loader():
        raise ValueError("sources.yaml: expected a non-empty 'sources' list")

    assert dashboard_app._count_reachable_sources(broken_loader) is None
    assert dashboard_app._count_reachable_sources() == sum(
        1 for source in load_sources() if source.blocked_channels != CLOUD_CHANNELS
    )


def test_reachable_source_count_keeps_a_store_blocked_on_one_channel_only():
    """Evophone-style store (blocked for daily, searchable from Streamlit
    Cloud) still counts as "in the radar"; a store blocked on both does not."""

    def make(slug: str, blocked: frozenset[str]) -> Source:
        return Source(
            slug=slug,
            name=slug,
            url=f"https://{slug}.example",
            platform="woocommerce",
            address="x",
            city="Rosario",
            trust_notes="t",
            blocked_channels=blocked,
        )

    registry = [
        make("open", frozenset()),
        make("daily-only", frozenset({"daily"})),
        make("quick-only", frozenset({"quick"})),
        make("both", CLOUD_CHANNELS),
    ]
    assert dashboard_app._count_reachable_sources(lambda: registry) == 3


def test_pages_carry_the_logo_title(seeded_db):
    at = _login(_app(seeded_db).run())
    assert not at.title
    titles = [m.value for m in at.markdown if "<h1>" in m.value]
    assert len(titles) == 1 and "<h1>Precios</h1>" in titles[0] and "<svg" in titles[0]


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
    assert ":green[↑ Ganás $64.300]" in body  # 85000 - 20700
    assert "Mejor precio en Celuphone (Incell/TFT)" in body
    assert "📍 Mejor precio" not in body  # the distance pill carries the pin
    assert "revisar" not in body


def test_home_card_caption_and_margin_lines():
    from repuestos_radar.dashboard import home

    assert home._best_caption("Celuphone", "Original", None) == (
        "Mejor precio en Celuphone (Original)"
    )
    assert home._best_caption("Celuphone", "Original", "1,8 km") == (
        "Mejor precio en Celuphone (Original) :gray-background[📍\u00a01,8 km]"
    )
    assert home._margin_line(Decimal("14300")) == ":green[↑ Ganás $14.300]"
    assert home._margin_line(Decimal("0")) == ":green[↑ Ganás $0]"
    assert home._margin_line(Decimal("-1200")) == ":red[↓ Perdés $1.200]"


def test_home_card_says_no_data_today_for_empty_item(seeded_db, monkeypatch):
    engine = get_engine(seeded_db)
    with get_session_factory(engine)() as session:
        session.add(TrackedItem(query="bateria iphone 11"))
        session.commit()
    at = _login(_app(seeded_db).run())
    body = _body(at)
    assert "sin datos de hoy" in body


class _RecordingCookieController:
    """Stub that remembers what the app asked the cookie component to do."""

    def __init__(self):
        self.store: dict[str, str] = {}
        self.set_calls: list[dict] = []
        self.removed: list[str] = []

    def get(self, name):
        return self.store.get(name)

    def set(self, name, value, **options):
        self.set_calls.append({"name": name, "value": value, **options})
        self.store[name] = value

    def remove(self, name):
        self.removed.append(name)
        self.store.pop(name, None)


def test_remember_me_cookie_is_secure_and_signed_with_the_cookie_secret(seeded_db, monkeypatch):
    from repuestos_radar.dashboard import auth

    monkeypatch.setenv("APP_COOKIE_SECRET", "cookie-secret-test")
    controller = _RecordingCookieController()
    monkeypatch.setattr(dashboard_app, "_cookie_controller", lambda: controller)

    at = _login(_app(seeded_db).run())
    assert at.session_state["authed"] is True
    (call,) = controller.set_calls
    assert call["secure"] is True
    assert auth.token_valid("clave-test", call["value"], secret="cookie-secret-test")
    assert not auth.token_valid("clave-test", call["value"])  # the secret is part of the key


def test_logout_button_clears_session_and_cookie(seeded_db, monkeypatch):
    controller = _RecordingCookieController()
    monkeypatch.setattr(dashboard_app, "_cookie_controller", lambda: controller)

    at = _login(_app(seeded_db).run())
    logout = [b for b in at.sidebar.button if b.label == "Salir"]
    assert len(logout) == 1
    logout[0].click().run()
    assert "authed" not in at.session_state or not at.session_state["authed"]
    assert controller.removed == ["repuestos_radar_session"]
    assert at.text_input  # back on the login form


def test_login_attempts_are_throttled_process_wide(seeded_db, monkeypatch):
    from repuestos_radar.dashboard import auth

    slept: list[float] = []
    fresh = auth.LoginThrottle(sleep=slept.append)
    monkeypatch.setattr(dashboard_app, "_THROTTLE", fresh)

    at = _app(seeded_db).run()
    for _ in range(4):
        at.text_input[0].set_value("wrong").run()
        at.button[0].set_value(True).run()
    assert slept == [0, 0, 0, 2]
    assert any("esperá" in e.value for e in at.error)  # the throttled note is shown
    assert "authed" not in at.session_state or not at.session_state["authed"]
