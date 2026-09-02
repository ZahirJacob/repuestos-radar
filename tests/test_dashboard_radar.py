"""Radar identity tests: pure HTML builders, pinned without Streamlit."""

import re

import pytest

from repuestos_radar.dashboard import radar, text_es

STATUS = "Desde Rosario · 6 tiendas en el radar"


@pytest.mark.parametrize(
    ("cx", "cy", "center", "period", "expected"),
    [
        # The three login blips and the two logo blips, exactly as in the
        # approved mockup: angle / 360 * period, expressed as a negative delay.
        (236, 92, (180, 130), 4.0, "-0.38s"),
        (128, 160, (180, 130), 4.0, "-2.33s"),
        (205, 196, (180, 130), 4.0, "-3.23s"),
        (27, 13, (20, 20), 3.0, "-0.38s"),
        (13, 25, (20, 20), 3.0, "-1.80s"),
    ],
)
def test_blip_delay_matches_the_mockup(cx, cy, center, period, expected):
    assert radar.blip_delay(cx, cy, center, period) == expected


def test_blip_delay_is_always_negative_and_within_one_period():
    for point in [(200, 130), (180, 10), (60, 130), (180, 250), (181, 130)]:
        delay = float(radar.blip_delay(*point, (180, 130), 4.0).rstrip("s"))
        assert -4.0 <= delay <= 0.0


def test_login_panel_has_the_radar_and_no_javascript():
    html = radar.login_panel_html(STATUS)
    assert "<svg" in html and "<style>" in html
    assert "<script" not in html and "onload" not in html
    assert radar.BLIP == "#ff2d2d" and html.count(radar.BLIP) >= 3
    assert "drop-shadow(0 0 4px #ff2d2d)" in html
    assert "@keyframes rr-login-sweep" in html and "rotate(360deg)" in html
    assert "animation:rr-login-sweep 4s linear infinite" in html
    assert 'style="transform-origin:180px 130px"' in html
    assert "prefers-reduced-motion: reduce" in html and "animation:none" in html
    assert 'r="4.5"' in html  # login blips
    assert html.count('class="rr-login-ping"') == 3


def test_login_panel_blips_use_the_negative_mockup_delays():
    html = radar.login_panel_html(STATUS)
    delays = re.findall(r"animation-delay:(-[\d.]+s)", html)
    # each of the three blips carries its delay on the ping ring and on the dot
    assert delays == ["-0.38s", "-0.38s", "-2.33s", "-2.33s", "-3.23s", "-3.23s"]


def test_login_panel_shows_brand_and_status_line():
    html = radar.login_panel_html(STATUS)
    assert text_es.APP_TITLE in html
    assert STATUS in html
    assert "monospace" in html  # system mono stack for the status line
    assert "fonts.googleapis" not in html


def test_login_panel_escapes_the_status_line():
    assert "&lt;b&gt;" in radar.login_panel_html("<b>")


def test_logo_svg_is_the_small_sweep():
    svg = radar.logo_svg()
    assert svg.startswith("<svg") and 'width="36" height="36"' in svg
    assert 'viewBox="0 0 40 40"' in svg
    assert svg.count('r="2.2"') == 2 and radar.BLIP in svg
    assert 'style="transform-origin:20px 20px"' in svg
    assert re.findall(r"animation-delay:(-[\d.]+s)", svg) == ["-0.38s", "-1.80s"]
    assert 'width="48" height="48"' in radar.logo_svg(48)


def test_svg_ids_are_prefixed_per_placement_so_they_never_collide():
    login = radar.login_panel_html(STATUS)
    logo = radar.logo_svg()
    assert set(re.findall(r'id="([^"]+)"', login)) == {"rr-login-wedge", "rr-login-disc"}
    assert set(re.findall(r'id="([^"]+)"', logo)) == {"rr-logo-wedge", "rr-logo-disc"}
    assert "url(#rr-login-disc)" in login and "url(#rr-login-wedge)" in login
    assert "url(#rr-logo-disc)" in logo and "url(#rr-logo-wedge)" in logo
    both = login + radar.page_title_html("x")
    assert "@keyframes rr-login-sweep" in both and "@keyframes rr-logo-sweep" in both


def test_page_title_is_logo_plus_h1():
    html = radar.page_title_html(text_es.NAV_PRICES)
    assert "<svg" in html
    assert f"<h1>{text_es.NAV_PRICES}</h1>" in html
    assert "animation:rr-logo-sweep 3s linear infinite" in html
    assert "prefers-reduced-motion: reduce" in html
    assert "<h1>&lt;i&gt;</h1>" in radar.page_title_html("<i>")


def test_html_has_no_blank_lines_for_the_markdown_renderer():
    # A blank line would end the markdown HTML block and split the panel.
    for html in (radar.login_panel_html(STATUS), radar.page_title_html("Precios")):
        assert "\n\n" not in html
