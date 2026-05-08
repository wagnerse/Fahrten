"""Erhebungsfahrten-Planer — Streamlit Web App entry point.

Thin orchestrator. The UI itself lives in `ui/`:
  ui/styles.py          — page config + CSS
  ui/state.py           — session-state init
  ui/errors.py          — copyable error dialog
  ui/sidebar.py         — sidebar (login, filters, load button, update, debug)
  ui/hero.py            — wordmark
  ui/optimization.py    — main pane (plan-strip → inputs → result → tour browser)
  ui/render.py          — DayPlan result rendering
  ui/map.py             — route-on-map visualization
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make this package's modules importable when bootstrapped by Streamlit.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import streamlit as st  # noqa: E402

from ui.hero import render_hero  # noqa: E402
from ui.optimization import render_optimization_section  # noqa: E402
from ui.sidebar import render_sidebar  # noqa: E402
from ui.state import init_session_state  # noqa: E402
from ui.styles import apply_page_config, inject_css  # noqa: E402


def main() -> None:
    apply_page_config()
    inject_css()
    init_session_state()

    sidebar_ctx = render_sidebar()

    render_hero()

    tours = st.session_state.tours
    if not tours:
        st.info("Lade zuerst Touren über die Sidebar (MyRES-Login).")
        return

    render_optimization_section(tours, sidebar_ctx)


main()
