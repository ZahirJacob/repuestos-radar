"""The committed Streamlit config keeps the hardening the client app relies on."""

import tomllib
from pathlib import Path

CONFIG = Path(__file__).resolve().parent.parent / ".streamlit" / "config.toml"


def test_error_details_stay_out_of_the_browser():
    config = tomllib.loads(CONFIG.read_text(encoding="utf-8"))
    assert config["client"]["showErrorDetails"] == "type"
    assert config["client"]["toolbarMode"] == "minimal"
    assert config["browser"]["gatherUsageStats"] is False
