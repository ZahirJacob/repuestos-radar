"""Detalle: part detail page. Full render arrives in Task 10 of this PR."""

import streamlit as st

from repuestos_radar.dashboard import text_es


def render() -> None:
    st.title(text_es.NAV_DETAIL)
