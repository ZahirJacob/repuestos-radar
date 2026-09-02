"""Detail-page tests: pure line-builders, pinned directly (AppTest can't
navigate to a function-based page here — see the module docstring in
detail.py and the note below)."""

from decimal import Decimal

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


def test_md_ars_escapes_the_dollar_for_markdown():
    assert detail.md_ars(Decimal("20700")) == "\\$20.700"


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
    state = {}
    reading = {"latitude": -32.95, "longitude": -60.65}
    # The user clicks the component button: the reading is adopted.
    assert detail._adopt_reading(state, reading) is True
    assert state["reference_point"] == (-32.95, -60.65)
    # Next rerun: the component replays the same reading — no change, no rerun.
    assert detail._adopt_reading(state, reading) is False


def test_adopt_reading_ignores_empty_or_null_reading():
    state = {}
    assert detail._adopt_reading(state, None) is False
    assert detail._adopt_reading(state, {"latitude": None, "longitude": None}) is False
    assert state == {}


def test_back_to_shop_survives_stale_component_reading():
    """Tap "Volver al local", then rerun: streamlit_geolocation replays its
    last reading on every rerun, and that stale reading must NOT be
    re-adopted — otherwise the button visibly does nothing (PR #21 blocker).
    """
    state = {}
    reading = {"latitude": -32.95, "longitude": -60.65}
    detail._adopt_reading(state, reading)  # user opted in earlier this visit
    state.pop("reference_point")  # user taps "Volver al local"
    # Rerun: the component still returns the old reading.
    assert detail._adopt_reading(state, reading) is False
    assert "reference_point" not in state


def test_distance_for_known_store():
    coords = {"celuphone": (-32.9386, -60.6801)}
    text = detail._distance_for("celuphone", (-32.9386, -60.6801), coords)
    assert text == "0 m"
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
    assert store_line == "[Celuphone](https://celuphone.com.ar/p/1) — 1,8 km"


def test_offer_line_warning_comes_after_the_store_line():
    offer = StoreOffer(
        source_slug="novocell",
        title="x",
        price=Decimal("9000"),
        url="https://n",
        relevance="low_confidence",
        tier="incell",
    )
    lines = detail._offer_line(offer, names={}, distance_text=None).split("\n")
    assert lines[0] == "#### \\$9.000"
    assert lines[1].startswith("[novocell](https://n)")
    assert lines[2].startswith(":orange[⚠")


def test_fair_price_highlight_wraps_the_line_in_a_blue_background():
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
        f":blue-background[{detail._fair_price_line(analysis)}]"
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
    assert detail._fair_price_highlight(single).startswith(":blue-background[*")
