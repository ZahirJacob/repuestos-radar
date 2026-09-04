"""Streamlit Cloud entry point for the PUBLIC DEMO: no password, sample data.

Deploy this file as the main file of a second Streamlit Cloud app (no
secrets needed), or run it locally with ``streamlit run demo_app.py``. The
client's app keeps ``streamlit_app.py``.
"""

from repuestos_radar.dashboard import demo

demo.configure_environment()

from repuestos_radar.dashboard.app import main  # noqa: E402  (after the flag is set)

main()
