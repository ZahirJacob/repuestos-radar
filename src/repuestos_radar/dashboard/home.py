"""Precios: one card per tracked part. Full render in the next task."""

import streamlit as st

from repuestos_radar.dashboard import text_es


def render() -> None:
    st.title(text_es.NAV_PRICES)
