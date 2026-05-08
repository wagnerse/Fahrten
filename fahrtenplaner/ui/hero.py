"""The wordmark / hero block at the top of the main pane."""

from __future__ import annotations

import streamlit as st

from updater import current_version


def render_hero() -> None:
    st.markdown(
        f"""
        <div class="studio-hero">
            <div class="eyebrow">
                <span class="db-mark">DB</span>
                <span>Zugzählung · Erhebungsfahrten</span>
                <span class="ver">v{current_version()}</span>
            </div>
            <h1 class="title">Fahrten<span class="accent">planer</span></h1>
            <p class="subtitle">Optimale Tourenketten für die DB-Zugzählung. Anreise,
            Touren, Transfers und Rückreise als ein durchgehender Tagesplan.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
