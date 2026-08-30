"""Smoke test: the package imports and its version matches the installed metadata."""

import importlib.metadata

import repuestos_radar


def test_package_imports_and_has_version() -> None:
    assert importlib.metadata.version("repuestos-radar") == repuestos_radar.__version__
