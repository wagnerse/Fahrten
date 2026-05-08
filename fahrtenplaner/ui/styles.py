"""Page configuration and CSS injection."""

from __future__ import annotations

from pathlib import Path

import streamlit as st


_CSS_PATH = Path(__file__).resolve().parent.parent / "assets" / "style.css"


def apply_page_config() -> None:
    st.set_page_config(
        page_title="Erhebungsfahrten-Planer",
        page_icon="🚂",
        layout="wide",
        initial_sidebar_state="expanded",
        menu_items={"Get help": None, "Report a bug": None, "About": None},
    )


@st.cache_data
def _load_stylesheet(_mtime_ns: int) -> str:
    """mtime_ns participates in the cache key so saving style.css invalidates the cache."""
    return _CSS_PATH.read_text(encoding="utf-8") if _CSS_PATH.exists() else ""


def inject_css() -> None:
    mtime_ns = _CSS_PATH.stat().st_mtime_ns if _CSS_PATH.exists() else 0
    css = _load_stylesheet(mtime_ns)
    if css:
        st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)
