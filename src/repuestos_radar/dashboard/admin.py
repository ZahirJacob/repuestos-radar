"""Ajustes: admin page (tracked items, service prices, quick search). Arrives in PR 4."""

import streamlit as st

from repuestos_radar.dashboard import text_es


def render() -> None:
    st.title(text_es.NAV_SETTINGS)
