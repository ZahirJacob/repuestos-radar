"""Smoke test: the package imports and reports the expected version."""

import repuestos_radar


def test_package_imports_and_has_version() -> None:
    assert repuestos_radar.__version__ == "0.1.0"
