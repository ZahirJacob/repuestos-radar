"""The committed Streamlit config keeps the hardening the client app relies on
and the design tokens of the approved "1a Fiel" direction."""

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / ".streamlit" / "config.toml"


def _config() -> dict:
    return tomllib.loads(CONFIG.read_text(encoding="utf-8"))


def test_error_details_stay_out_of_the_browser():
    config = _config()
    assert config["client"]["showErrorDetails"] == "type"
    assert config["client"]["toolbarMode"] == "minimal"
    assert config["browser"]["gatherUsageStats"] is False


def test_inter_is_served_from_the_repo_not_from_google():
    """One vendored woff2 (Latin, variable 400-700, OFL) through Streamlit's
    static serving: cached by the browser after the first visit, no
    third-party font host in the client's app."""
    config = _config()
    assert config["server"]["enableStaticServing"] is True
    (face,) = config["theme"]["fontFaces"]
    assert face["family"] == "Inter"
    assert face["url"].startswith("app/static/")
    assert (ROOT / "static" / face["url"].removeprefix("app/static/")).is_file()
    assert (ROOT / "static" / "fonts" / "Inter-LICENSE.txt").is_file()
    assert face["weight"] == "400 700"
    assert config["theme"]["font"].startswith("Inter")
    assert config["theme"]["headingFont"].startswith("Inter")


def test_heading_scale_follows_the_design():
    """h1 title 34, h2 card price 40, h3 tier heading 24, h4 offer price 28,
    h5 card query 21 — the design's sizes, as rem of the 16px base."""
    theme = _config()["theme"]
    assert theme["headingFontSizes"] == [
        "2.125rem",
        "2.5rem",
        "1.5rem",
        "1.75rem",
        "1.3125rem",
        "1rem",
    ]
    assert theme["headingFontWeights"] == [600, 600, 600, 600, 500, 500]


def test_dark_theme_is_the_radar_ground():
    dark = _config()["theme"]["dark"]
    assert dark["backgroundColor"] == "#0d2a2a"
    assert dark["secondaryBackgroundColor"] == "#123632"
    assert dark["primaryColor"] == "#38d67a"
    assert dark["textColor"] == "#e8efec"
    # Pills and bands: gray for distance, orange for warnings, green for the fair price.
    for role in ("grayBackgroundColor", "orangeBackgroundColor", "greenBackgroundColor"):
        assert dark[role].startswith("#"), role
    assert dark["greenTextColor"] == "#baf5d0"
    assert dark["orangeTextColor"] == "#ffb347"
