"""The radar visual identity: a sweeping SVG radar with red blips, no JavaScript.

Ported from the approved mockup. Pure functions build the HTML (so tests need
no Streamlit); ``page_title`` and ``render_login_panel`` are the thin render
helpers the pages call.

Why ``st.markdown(unsafe_allow_html=True)`` and not ``st.html``: the ``st.html``
frontend sanitizes with DOMPurify using ``USE_PROFILES: {html: true}``, and that
profile does not include the SVG tags — the whole radar would be stripped.
Markdown's raw-HTML path keeps ``<style>`` and ``<svg>`` intact.
"""

import math
from html import escape

import streamlit as st

from repuestos_radar.dashboard import text_es

GROUND = "#0d2a2a"
GROUND_2 = "#123632"
RADAR = "#38d67a"
BLIP = "#ff2d2d"

LOGIN_PERIOD_S = 4.0
LOGO_PERIOD_S = 3.0

_MONO = "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"

# Radar centers and blip positions come straight from the approved mockup.
_LOGIN_CENTER = (180.0, 130.0)
_LOGIN_BLIPS = ((236.0, 92.0), (128.0, 160.0), (205.0, 196.0))
_LOGO_CENTER = (20.0, 20.0)
_LOGO_BLIPS = ((27.0, 13.0), (13.0, 25.0))


def blip_delay(cx: float, cy: float, center: tuple[float, float], period_s: float) -> str:
    """The negative ``animation-delay`` that makes a blip flash as the sweep passes.

    The sweep line starts at 3 o'clock and turns clockwise, one turn per
    ``period_s``; a blip at screen angle ``a`` (clockwise from 3 o'clock, y
    pointing down as in SVG) is reached ``a / 360 * period_s`` into the turn.
    Written as a negative delay — the blip animation "already ran" for the
    rest of the turn — so even the very first sweep hits it in sync.
    """
    angle = math.degrees(math.atan2(cy - center[1], cx - center[0])) % 360
    return f"{angle / 360 * period_s - period_s:.2f}s"


def _animation_css(prefix: str, period_s: float) -> str:
    """Sweep, blip and ping keyframes for one radar placement.

    Class and keyframe names carry the placement prefix so the login panel
    and the logo never share a rule; ``prefers-reduced-motion`` stops every
    animation and leaves the radar as a still picture.
    """
    period = f"{period_s:g}s"
    return (
        f".{prefix}-sweep{{transform-origin:50% 50%;"
        f"animation:{prefix}-sweep {period} linear infinite}}"
        f"@keyframes {prefix}-sweep{{to{{transform:rotate(360deg)}}}}"
        f".{prefix}-blip{{animation:{prefix}-blip {period} linear infinite;"
        f"filter:drop-shadow(0 0 4px {BLIP}) drop-shadow(0 0 1px {BLIP})}}"
        f"@keyframes {prefix}-blip{{0%{{opacity:.3}}2%{{opacity:1}}55%{{opacity:.7}}"
        f"100%{{opacity:.3}}}}"
        f".{prefix}-ping{{animation:{prefix}-ping {period} ease-out infinite}}"
        f"@keyframes {prefix}-ping{{0%{{transform:scale(.3);opacity:.9}}"
        f"30%{{transform:scale(1.6);opacity:0}}100%{{transform:scale(1.6);opacity:0}}}}"
        f"@media (prefers-reduced-motion: reduce){{"
        f".{prefix}-sweep,.{prefix}-blip,.{prefix}-ping{{animation:none}}"
        f".{prefix}-blip{{opacity:.9}}.{prefix}-ping{{opacity:0}}}}"
    )


def _login_svg() -> str:
    p = "rr-login"
    blips = "".join(
        f'<g><circle class="{p}-ping" cx="{cx:g}" cy="{cy:g}" r="9" fill="none" '
        f'stroke="{BLIP}" stroke-width="2" style="transform-origin:{cx:g}px {cy:g}px;'
        f'animation-delay:{delay}"/>'
        f'<circle class="{p}-blip" cx="{cx:g}" cy="{cy:g}" r="4.5" '
        f'style="animation-delay:{delay}"/></g>'
        for cx, cy in _LOGIN_BLIPS
        for delay in (blip_delay(cx, cy, _LOGIN_CENTER, LOGIN_PERIOD_S),)
    )
    return (
        f'<svg viewBox="0 0 360 300" aria-hidden="true">'
        f"<defs>"
        f'<linearGradient id="{p}-wedge" x1="0" y1="0" x2="1" y2="0">'
        f'<stop offset="0" stop-color="{RADAR}" stop-opacity="0"/>'
        f'<stop offset="1" stop-color="{RADAR}" stop-opacity=".55"/>'
        f"</linearGradient>"
        f'<clipPath id="{p}-disc"><circle cx="180" cy="130" r="118"/></clipPath>'
        f"</defs>"
        f'<g fill="none" stroke="{RADAR}" stroke-opacity=".22" stroke-width="1">'
        f'<circle cx="180" cy="130" r="118"/><circle cx="180" cy="130" r="88"/>'
        f'<circle cx="180" cy="130" r="58"/><circle cx="180" cy="130" r="28"/>'
        f'<line x1="62" y1="130" x2="298" y2="130"/><line x1="180" y1="12" x2="180" y2="248"/>'
        f"</g>"
        f'<g clip-path="url(#{p}-disc)">'
        f'<g class="{p}-sweep" style="transform-origin:180px 130px">'
        f'<path d="M180 130 L298 130 A118 118 0 0 0 274 61 Z" fill="url(#{p}-wedge)"/>'
        f'<line x1="180" y1="130" x2="298" y2="130" stroke="{RADAR}" stroke-width="2" '
        f'stroke-opacity=".9"/>'
        f"</g></g>"
        f'<g fill="{BLIP}">{blips}</g>'
        f'<circle cx="180" cy="130" r="3" fill="{RADAR}"/>'
        f"</svg>"
    )


def login_panel_html(status_line: str) -> str:
    """The dark radar panel shown above the password form: radar, brand, status."""
    css = (
        _animation_css("rr-login", LOGIN_PERIOD_S)
        + ".rr-login{position:relative;height:300px;overflow:hidden;box-sizing:border-box;"
        "display:flex;flex-direction:column;justify-content:flex-end;padding:18px 20px;"
        "border-radius:.5rem;"
        f"background:radial-gradient(circle at 50% 42%,{GROUND_2},{GROUND} 70%)}}"
        ".rr-login svg{position:absolute;inset:0;width:100%;height:100%}"
        ".rr-login-brand{position:relative;color:#f2fbf5;font-weight:700;font-size:1.7rem;"
        "line-height:1.1;margin:0}"
        f".rr-login-status{{position:relative;font-family:{_MONO};font-size:.72rem;"
        f"letter-spacing:.1em;text-transform:uppercase;color:{RADAR};margin:6px 0 0}}"
    )
    return (
        f"<style>{css}</style>"
        f'<div class="rr-login">{_login_svg()}'
        f'<div class="rr-login-brand">{escape(text_es.APP_TITLE)}</div>'
        f'<div class="rr-login-status">{escape(status_line)}</div>'
        f"</div>"
    )


def logo_svg(size_px: int = 36) -> str:
    """The small sweeping radar shown beside every page title."""
    p = "rr-logo"
    blips = "".join(
        f'<circle class="{p}-blip" cx="{cx:g}" cy="{cy:g}" r="2.2" fill="{BLIP}" '
        f'style="animation-delay:{blip_delay(cx, cy, _LOGO_CENTER, LOGO_PERIOD_S)}"/>'
        for cx, cy in _LOGO_BLIPS
    )
    return (
        f'<svg class="{p}" width="{size_px}" height="{size_px}" viewBox="0 0 40 40" '
        f'aria-hidden="true">'
        f"<defs>"
        f'<linearGradient id="{p}-wedge" x1="0" y1="0" x2="1" y2="0">'
        f'<stop offset="0" stop-color="{RADAR}" stop-opacity="0"/>'
        f'<stop offset="1" stop-color="{RADAR}" stop-opacity=".7"/>'
        f"</linearGradient>"
        f'<clipPath id="{p}-disc"><circle cx="20" cy="20" r="18"/></clipPath>'
        f"</defs>"
        f'<circle cx="20" cy="20" r="18" fill="{GROUND}"/>'
        f'<g fill="none" stroke="{RADAR}" stroke-opacity=".35" stroke-width="1">'
        f'<circle cx="20" cy="20" r="12"/><circle cx="20" cy="20" r="6"/>'
        f'<line x1="2" y1="20" x2="38" y2="20"/><line x1="20" y1="2" x2="20" y2="38"/>'
        f"</g>"
        f'<g clip-path="url(#{p}-disc)">'
        f'<g class="{p}-sweep" style="transform-origin:20px 20px">'
        f'<path d="M20 20 L38 20 A18 18 0 0 0 33 8 Z" fill="url(#{p}-wedge)"/>'
        f'<line x1="20" y1="20" x2="38" y2="20" stroke="{RADAR}" stroke-width="1.6"/>'
        f"</g></g>"
        f"{blips}"
        f'<circle cx="20" cy="20" r="1.4" fill="{RADAR}"/>'
        f"</svg>"
    )


def page_title_html(title: str, size_px: int = 36) -> str:
    """Logo + title in one row.

    The title is a real ``<h1>`` so Streamlit's markdown renderer styles it
    exactly like ``st.title`` (same size, weight, padding, anchor) — the pages
    must not look different from before, only gain the logo.
    """
    css = (
        _animation_css("rr-logo", LOGO_PERIOD_S)
        + ".rr-titlebar{display:flex;align-items:center;gap:12px}"
        ".rr-titlebar svg{flex:none}"
    )
    return (
        f"<style>{css}</style>"
        f'<div class="rr-titlebar">{logo_svg(size_px)}<h1>{escape(title)}</h1></div>'
    )


def _render_html(html: str) -> None:
    # See the module docstring for why this is not st.html.
    st.markdown(html, unsafe_allow_html=True)


def render_login_panel(status_line: str) -> None:
    _render_html(login_panel_html(status_line))


def page_title(title: str) -> None:
    """Drop-in for ``st.title(title)`` with the sweeping logo in front."""
    _render_html(page_title_html(title))
