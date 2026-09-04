"""The dashboard's strings in the language the visitor picked.

``t`` looks like ``text_es`` (``t.APP_TITLE``, ``t.BEST_CAPTION`` …) but
reads each name from the module for the current language, so pages never
import a language module directly. The language lives in
``st.session_state["lang"]`` (default Spanish, the client's language) and is
only ever changed by the public demo's toggle; ``?lang=en`` in the URL picks
it for a shared link.
"""

import streamlit as st

from repuestos_radar.dashboard import text_en, text_es

DEFAULT_LANGUAGE = "es"
LANGUAGES = {"es": text_es, "en": text_en}
_STATE_KEY = "lang"


def current_language(state=None) -> str:
    """The active language code; unknown or missing values fall back to Spanish."""
    if state is None:
        state = st.session_state
    lang = state.get(_STATE_KEY, DEFAULT_LANGUAGE)
    return lang if lang in LANGUAGES else DEFAULT_LANGUAGE


def set_language(lang: str, state=None) -> None:
    if lang not in LANGUAGES:
        raise ValueError(f"unknown language {lang!r} (expected {sorted(LANGUAGES)})")
    if state is None:
        state = st.session_state
    state[_STATE_KEY] = lang


class _Strings:
    def __getattr__(self, name: str):
        return getattr(LANGUAGES[current_language()], name)


t = _Strings()
