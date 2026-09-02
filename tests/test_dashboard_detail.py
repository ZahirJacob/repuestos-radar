"""Detail-page tests: pure line-builders, pinned directly (AppTest can't
navigate to a function-based page here — see the module docstring in
detail.py and the note below)."""

from decimal import Decimal

from repuestos_radar.analysis import BASIS_MEDIAN, BASIS_SINGLE_STORE, StoreOffer, TierAnalysis
from repuestos_radar.dashboard import detail

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
    assert "$22.100" in line and "entre $20.700 y $23.500" in line


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
