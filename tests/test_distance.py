"""Straight-line distance for the dashboard (no routing, no maps API)."""

import pytest

from repuestos_radar.dashboard.distance import format_distance_km, haversine_km, shop_location


def test_haversine_zero_for_same_point():
    assert haversine_km(-32.95, -60.65, -32.95, -60.65) == pytest.approx(0.0)


def test_haversine_known_pair():
    # Rosario Monumento a la Bandera -> Buenos Aires Obelisco, ~278 km straight line.
    km = haversine_km(-32.9478, -60.6305, -34.6037, -58.3816)
    assert km == pytest.approx(278, rel=0.02)


def test_haversine_short_city_hop():
    # ~1 degree of longitude at this latitude is ~93 km; 0.01 deg ~ 0.93 km.
    km = haversine_km(-32.95, -60.65, -32.95, -60.64)
    assert km == pytest.approx(0.93, rel=0.05)


@pytest.mark.parametrize(
    ("km", "expected"),
    [
        (0.85, "850\u00a0m"),
        (0.9999, "1,0\u00a0km"),  # rounds to 1000 m -> promoted to km
        (0.049, "50\u00a0m"),
        (2.14, "2,1\u00a0km"),
        (9.96, "10\u00a0km"),  # rounds to 10.0 -> promoted to integer km
        (12.4, "12\u00a0km"),
        (278.6, "279\u00a0km"),
    ],
)
def test_format_distance(km, expected):
    # The unit is joined with a non-breaking space so the pill never wraps.
    assert format_distance_km(km) == expected


def test_shop_location_reads_env(monkeypatch):
    monkeypatch.setenv("SHOP_LAT", "-32.95")
    monkeypatch.setenv("SHOP_LON", "-60.65")
    assert shop_location() == (-32.95, -60.65)


@pytest.mark.parametrize(
    ("lat", "lon"),
    [(None, None), ("-32.95", None), ("abc", "-60.65"), ("-95", "-60.65")],
)
def test_shop_location_missing_or_invalid_is_none(monkeypatch, lat, lon):
    for name, value in (("SHOP_LAT", lat), ("SHOP_LON", lon)):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    assert shop_location() is None
