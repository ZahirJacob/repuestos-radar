"""Detail-page tests: pure line-builders, pinned directly (AppTest can't
navigate to a function-based page here — see the module docstring in
detail.py and the note below)."""

from decimal import Decimal

import pytest
from streamlit.testing.v1 import AppTest

from repuestos_radar.analysis import BASIS_MEDIAN, BASIS_SINGLE_STORE, StoreOffer, TierAnalysis
from repuestos_radar.dashboard import detail, text_es

# AppTest note: `st.switch_page` in the installed Streamlit version only
# switches to file-based pages ("path relative to the main script's
# location"); our pages are function-based (`st.Page(home.render, ...)`),
# so there is no file path to switch to, and `AppTest.from_function` runs
# the function body in isolation without its module's imports (`st` itself
# is undefined inside it). Neither route reaches detail.render() through
# AppTest. Per the task brief, detail.py's rendering logic is instead
# factored into module-level pure functions (`_offer_line`,
# `_fair_price_line`) and pinned directly, which is real coverage of
# everything render() does except the Streamlit layout calls themselves.


def test_offer_line_plain_match():
    line = detail._offer_line(
        offer=StoreOffer(
            source_slug="celuphone",
            title="Modulo A32 incell",
            price=Decimal("20700"),
            url="https://celuphone.com.ar/p/1",
            relevance="match",
            tier="incell",
        ),
        names={"celuphone": "Celuphone"},
        distance_text=None,
    )
    assert "[Celuphone](https://celuphone.com.ar/p/1)" in line
    assert "$20.700" in line
    assert "⚠" not in line  # no warning marker for a plain match


def test_offer_line_low_confidence_and_outlier_warn():
    offer = StoreOffer(
        source_slug="novocell",
        title="x",
        price=Decimal("9000"),
        url="https://n",
        relevance="low_confidence",
        tier="incell",
        outlier=True,
    )
    line = detail._offer_line(offer, names={}, distance_text=None)
    assert "⚠" in line and "novocell" in line
    # both warnings compose once, cleanly — no doubled "revisar:" lead-in
    assert "muy alejado del resto" in line
    assert "otro modelo" in line
    assert line.count("revisar") == 0


def test_fair_price_line_small_sample_shows_range():
    analysis = TierAnalysis(
        tier="incell",
        offers=(),
        fair_price=Decimal("22100"),
        price_min=Decimal("20700"),
        price_max=Decimal("23500"),
        store_count=2,
        basis=BASIS_MEDIAN,
    )
    line = detail._fair_price_line(analysis)
    # dollars escaped: Streamlit markdown would otherwise read "$...$" as LaTeX
    assert "**\\$22.100**" in line and "entre \\$20.700 y \\$23.500" in line
    assert "$$" not in line


def test_margin_line_keeps_both_escaped_dollar_signs():
    from repuestos_radar.margin import TierMargin
    from repuestos_radar.models import ServicePrice

    service = ServicePrice(tracked_item_id=1, label="Cambio módulo A32", price_ars=Decimal("85000"))
    tier_margin = TierMargin(
        tier="incell", part_source="celuphone", part_price=Decimal("20700"), margin=Decimal("64300")
    )
    line = detail._margin_line(service, tier_margin, {"celuphone": "Celuphone"})
    assert line == (
        "Cambio módulo A32 (\\$85.000): ganás \\$64.300 con el repuesto de Celuphone (Incell/TFT)"
    )
    assert line.count("\\$") == 2 and "$$" not in line
    loss = TierMargin(
        tier="incell", part_source="celuphone", part_price=Decimal("90000"), margin=Decimal("-5000")
    )
    assert "perdés \\$5.000" in detail._margin_line(service, loss, {})
    dollar_label = ServicePrice(
        tracked_item_id=1, label="Promo $ finde", price_ars=Decimal("85000")
    )
    assert detail._margin_line(dollar_label, tier_margin, {}).startswith("Promo \\$ finde (")


def test_fair_price_line_single_store_is_honest():
    analysis = TierAnalysis(
        tier="incell",
        offers=(),
        fair_price=None,
        price_min=Decimal("20700"),
        price_max=Decimal("20700"),
        store_count=1,
        basis=BASIS_SINGLE_STORE,
    )
    assert "una sola tienda" in detail._fair_price_line(analysis)


def test_adopt_reading_takes_new_reading_once():
    """Within one request, the same answer handed back on a later rerun (any
    other widget can cause one while the component is mounted) counts once."""
    state = {}
    reading = {"latitude": -32.95, "longitude": -60.65}
    assert detail._adopt_reading(state, reading) is True
    assert state["reference_point"] == (-32.95, -60.65)
    assert detail._adopt_reading(state, reading) is False
    # A different reading within the same request is still taken.
    assert detail._adopt_reading(state, {"latitude": -32.96, "longitude": -60.65}) is True
    assert state["reference_point"] == (-32.96, -60.65)


def test_adopt_reading_ignores_empty_or_null_reading():
    state = {}
    assert detail._adopt_reading(state, None) is False
    assert detail._adopt_reading(state, {"latitude": None, "longitude": None}) is False
    assert state == {}


def test_back_to_shop_clears_pending_and_denied_requests_but_keeps_nothing_stored():
    state = {"reference_point": (1.0, 2.0), "geo_requested": True, "geo_denied": True}
    detail._back_to_shop(state)
    assert state == {}
    detail._back_to_shop(state)  # idempotent: a second tap from the shop is a no-op
    assert state == {}


def test_request_location_asks_for_a_fresh_reading_each_tap():
    """Each tap bumps the component key (a fresh iframe asks the browser
    again) and resets the adopted-reading baseline: after "Volver al local",
    a second tap from the very same spot must take effect again."""
    state = {}
    reading = {"latitude": -32.95, "longitude": -60.65}
    detail._request_location(state)
    assert state["geo_requested"] is True and state["geo_request_id"] == 1
    assert detail._adopt_reading(state, reading) is True
    detail._back_to_shop(state)
    detail._request_location(state)
    assert state["geo_request_id"] == 2
    assert "geo_denied" not in state
    assert detail._adopt_reading(state, reading) is True  # same spot, adopted again
    assert state["reference_point"] == (-32.95, -60.65)


def test_reading_from_answer_flattens_the_component_shape():
    answer = {
        "coords": {"latitude": -32.95, "longitude": -60.65, "accuracy": 20.0},
        "timestamp": 1_700_000_000,
    }
    assert detail._reading_from_answer(answer) == {"latitude": -32.95, "longitude": -60.65}
    assert detail._reading_from_answer(None) is None  # browser has not answered yet
    assert detail._reading_from_answer({"error": {"code": 1, "message": "denied"}}) is None
    assert detail._reading_from_answer("garbage") is None
    # Numeric strings are coerced (the JSON bridge may hand them over as text).
    assert detail._reading_from_answer(
        {"coords": {"latitude": "-32.95", "longitude": "-60.65"}}
    ) == {
        "latitude": -32.95,
        "longitude": -60.65,
    }
    assert detail._answer_is_denied({"error": {"code": 1, "message": "denied"}}) is True
    assert detail._answer_is_denied(answer) is False
    assert detail._answer_is_denied(None) is False


@pytest.mark.parametrize(
    "coords",
    [
        {"latitude": -32.95},  # longitude missing
        {"latitude": None, "longitude": -60.65},
        {"latitude": "sur", "longitude": -60.65},  # not a number
        {"latitude": float("nan"), "longitude": -60.65},
        {"latitude": -32.95, "longitude": float("inf")},
        {"latitude": 90.5, "longitude": -60.65},  # off the globe
        {"latitude": -32.95, "longitude": -180.5},
        [-32.95, -60.65],  # not even a mapping
    ],
)
def test_reading_from_answer_rejects_bad_coordinates(coords):
    assert detail._reading_from_answer({"coords": coords}) is None


@pytest.fixture()
def no_component(monkeypatch):
    """Make ``import streamlit_js_eval`` fail, as on a broken deploy."""
    import builtins

    real_import = builtins.__import__

    def failing_import(name, *args, **kwargs):
        if name == "streamlit_js_eval":
            raise ImportError("component missing")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", failing_import)


def test_ask_browser_degrades_to_no_answer_without_the_component(no_component):
    assert detail._geolocation() is None
    assert detail._ask_browser(1) is None


def test_location_button_is_disabled_without_the_component(no_component):
    at = AppTest.from_string(_REFERENCE_POINT_SCRIPT, default_timeout=10).run()
    assert not at.exception
    use, back = at.button
    assert use.label == text_es.USE_MY_LOCATION and use.disabled is True
    assert back.label == text_es.BACK_TO_SHOP and back.disabled is False


_REFERENCE_POINT_SCRIPT = """
import streamlit as st
from repuestos_radar.dashboard import detail

st.session_state["reference"] = detail._reference_point()
"""


def _reference_point_app(monkeypatch, answers: dict[int, object]) -> AppTest:
    """``_reference_point`` alone, with the browser stubbed: ``answers`` maps
    a request id to what the component returns for it."""
    monkeypatch.setenv("SHOP_LAT", "-32.9468")
    monkeypatch.setenv("SHOP_LON", "-60.6393")
    monkeypatch.setattr(detail, "_ask_browser", lambda request_id: answers.get(request_id))
    return AppTest.from_string(_REFERENCE_POINT_SCRIPT, default_timeout=10)


def _from_line(at: AppTest) -> str:
    return at.markdown[0].value


def test_reference_point_tap_answer_adopt_then_back_to_shop(monkeypatch):
    answers: dict[int, object] = {}
    at = _reference_point_app(monkeypatch, answers).run()
    assert not at.exception
    assert _from_line(at) == f"📍 {text_es.FROM_SHOP}"
    assert at.session_state["reference"] == (-32.9468, -60.6393)

    # Tap: the request is pending, the browser has not answered yet.
    at.button[0].click().run()
    assert at.session_state["geo_requested"] is True
    assert at.session_state["geo_request_id"] == 1
    assert _from_line(at) == f"📍 {text_es.FROM_SHOP}"

    # The answer arrives on a later rerun: adopted, component unmounted.
    answers[1] = {"coords": {"latitude": -32.95, "longitude": -60.65}}
    at.run()
    assert not at.exception
    assert _from_line(at) == f"📍 {text_es.FROM_MY_LOCATION}"
    assert at.session_state["reference"] == (-32.95, -60.65)
    assert at.session_state["reference_point"] == (-32.95, -60.65)
    assert "geo_requested" not in at.session_state

    # "Volver al local": back to the shop, and the old answer never returns.
    at.button[1].click().run()
    assert _from_line(at) == f"📍 {text_es.FROM_SHOP}"
    assert at.session_state["reference"] == (-32.9468, -60.6393)
    assert "reference_point" not in at.session_state
    at.run()  # one more rerun: still the shop
    assert _from_line(at) == f"📍 {text_es.FROM_SHOP}"

    # A second tap from the same spot is adopted again (fresh request id).
    at.button[0].click().run()
    assert at.session_state["geo_request_id"] == 2
    answers[2] = {"coords": {"latitude": -32.95, "longitude": -60.65}}
    at.run()
    assert _from_line(at) == f"📍 {text_es.FROM_MY_LOCATION}"


def test_reference_point_tap_then_back_ignores_a_late_answer(monkeypatch):
    answers: dict[int, object] = {}
    at = _reference_point_app(monkeypatch, answers).run()
    at.button[0].click().run()  # tap: pending
    at.button[1].click().run()  # back to the shop before the browser answers
    assert "geo_requested" not in at.session_state
    answers[1] = {"coords": {"latitude": -32.95, "longitude": -60.65}}  # late answer
    at.run()
    assert not at.exception
    assert _from_line(at) == f"📍 {text_es.FROM_SHOP}"
    assert "reference_point" not in at.session_state
    assert at.session_state["reference"] == (-32.9468, -60.6393)


def test_reference_point_denied_answer_shows_the_caption(monkeypatch):
    answers: dict[int, object] = {1: {"error": {"code": 1, "message": "User denied"}}}
    at = _reference_point_app(monkeypatch, answers).run()
    at.button[0].click().run()
    assert not at.exception
    assert at.session_state["geo_denied"] is True
    assert "geo_requested" not in at.session_state
    assert [c.value for c in at.caption] == [text_es.LOCATION_DENIED]
    assert _from_line(at) == f"📍 {text_es.FROM_SHOP}"
    at.button[1].click().run()  # back to the shop clears the note
    assert not at.caption


def test_distance_pill_and_distance_from_shop(monkeypatch):
    # Non-breaking space after the pin so the pill never wraps on a phone.
    assert detail.distance_pill("1,8 km") == ":gray-background[📍\u00a01,8 km]"
    monkeypatch.setenv("SHOP_LAT", "-32.9468")
    monkeypatch.setenv("SHOP_LON", "-60.6393")
    coords = detail._store_coords()
    known = next(iter(coords))
    assert detail.distance_from_shop(known) == detail._distance_for(
        known, (-32.9468, -60.6393), coords
    )
    assert detail.distance_from_shop("nowhere") is None
    monkeypatch.delenv("SHOP_LAT")
    monkeypatch.delenv("SHOP_LON")
    assert detail.distance_from_shop(known) is None


def test_tier_heading_counts_stores():
    def analysis(count):
        return TierAnalysis(
            tier="original",
            offers=(),
            fair_price=None,
            price_min=Decimal("1"),
            price_max=Decimal("1"),
            store_count=count,
            basis=BASIS_MEDIAN,
        )

    assert detail._tier_heading(analysis(3)) == "Original :gray[· 3 tiendas]"
    assert detail._tier_heading(analysis(1)) == "Original :gray[· 1 tienda]"


def test_sort_key_falls_back_to_price():
    assert detail._sort_key(text_es.SORT_DISTANCE) == "distancia"
    assert detail._sort_key(text_es.SORT_PRICE) == "precio"
    assert detail._sort_key(None) == "precio"  # segmented control deselected


def test_distance_for_known_store():
    coords = {"celuphone": (-32.9386, -60.6801)}
    text = detail._distance_for("celuphone", (-32.9386, -60.6801), coords)
    assert text == "0\u00a0m"
    assert detail._distance_for("nowhere", (-32.9386, -60.6801), coords) is None
    assert detail._distance_for("celuphone", None, coords) is None


def test_sorted_offers_by_distance_puts_unknown_last():
    coords = {"near": (-32.95, -60.65), "far": (-34.60, -58.38)}
    near = StoreOffer(
        source_slug="near",
        title="a",
        price=Decimal("30000"),
        url="u",
        relevance="match",
        tier="incell",
    )
    far = StoreOffer(
        source_slug="far",
        title="b",
        price=Decimal("10000"),
        url="u",
        relevance="match",
        tier="incell",
    )
    unknown = StoreOffer(
        source_slug="web",
        title="c",
        price=Decimal("20000"),
        url="u",
        relevance="match",
        tier="incell",
    )
    result = detail._sorted_offers((far, unknown, near), "distancia", (-32.95, -60.65), coords)
    assert [o.source_slug for o in result] == ["near", "far", "web"]
    by_price = detail._sorted_offers((far, unknown, near), "precio", (-32.95, -60.65), coords)
    assert [o.source_slug for o in by_price] == ["far", "web", "near"]


def test_trend_chart_is_static_and_uses_the_spanish_columns():
    """The trend is an Altair chart with no tooltip and no pan/zoom: the
    built-in st.line_chart tooltip stuck open after a touch on the client's
    phone, and this spec is what keeps that from coming back."""
    from datetime import date
    from decimal import Decimal

    from repuestos_radar.dashboard.detail import _trend_chart

    spec = _trend_chart(
        [(date(2026, 8, 30), Decimal("20700")), (date(2026, 9, 1), Decimal("21500.50"))]
    ).to_dict()

    assert spec["mark"] == {"type": "line", "point": True}
    assert "tooltip" not in spec["encoding"]
    assert "params" not in spec  # .interactive() would add selection params
    x, y = spec["encoding"]["x"], spec["encoding"]["y"]
    assert x["field"] == text_es.TREND_CHART_DAY_COLUMN
    assert x["type"] == "temporal"
    assert x["title"] == text_es.TREND_CHART_DAY_COLUMN
    assert x["axis"] == {"format": "%d/%m"}
    assert y["field"] == text_es.TREND_CHART_PRICE_COLUMN
    assert y["type"] == "quantitative"
    assert y["title"] == text_es.TREND_CHART_PRICE_COLUMN
    (rows,) = spec["datasets"].values()
    assert [row[text_es.TREND_CHART_PRICE_COLUMN] for row in rows] == [20700.0, 21500.5]


def test_offer_line_puts_the_price_first_as_a_heading():
    line = detail._offer_line(
        offer=StoreOffer(
            source_slug="celuphone",
            title="Modulo A32 incell",
            price=Decimal("20700"),
            url="https://celuphone.com.ar/p/1",
            relevance="match",
            tier="incell",
        ),
        names={"celuphone": "Celuphone"},
        distance_text="1,8 km",
    )
    price_line, store_line = line.split("\n")
    assert price_line == "#### \\$20.700"
    assert store_line == (
        "[Celuphone](https://celuphone.com.ar/p/1) :gray-background[📍\u00a01,8 km]"
    )


def test_offer_line_warnings_are_orange_pills_on_the_store_line():
    offer = StoreOffer(
        source_slug="novocell",
        title="x",
        price=Decimal("9000"),
        url="https://n",
        relevance="low_confidence",
        tier="incell",
        outlier=True,
    )
    lines = detail._offer_line(offer, names={}, distance_text="3,4 km").split("\n")
    assert lines == [
        "#### \\$9.000",
        "[novocell](https://n) :gray-background[📍\u00a03,4 km] "
        f":orange-background[⚠\u00a0{text_es.OUTLIER_WARNING}] "
        f":orange-background[⚠\u00a0{text_es.LOW_CONFIDENCE_WARNING}]",
    ]


def test_fair_price_highlight_wraps_the_line_in_a_green_background():
    analysis = TierAnalysis(
        tier="incell",
        offers=(),
        fair_price=Decimal("20000"),
        price_min=Decimal("18000"),
        price_max=Decimal("22000"),
        store_count=4,
        basis=BASIS_MEDIAN,
    )
    assert detail._fair_price_highlight(analysis) == (
        f":green-background[{detail._fair_price_line(analysis)}]"
    )
    single = TierAnalysis(
        tier="incell",
        offers=(),
        fair_price=None,
        price_min=Decimal("20000"),
        price_max=Decimal("20000"),
        store_count=1,
        basis=BASIS_SINGLE_STORE,
    )
    assert detail._fair_price_highlight(single).startswith(":green-background[*")
