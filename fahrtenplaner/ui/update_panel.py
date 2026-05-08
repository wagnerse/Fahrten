"""Sidebar 'Über / Update' panel — version display and one-click in-app update."""

from __future__ import annotations

import streamlit as st

from updater import (
    GITHUB_REPO,
    current_version,
    download_update,
    fetch_latest_release,
    is_newer,
)
from .errors import report_error


@st.cache_data(ttl=600, show_spinner=False)
def _check_for_update(_repo: str):
    return fetch_latest_release()


def render_update_panel() -> None:
    with st.sidebar.expander(f"Über / Update — v{current_version()}"):
        if GITHUB_REPO == "OWNER/REPO":
            st.caption("Auto-Update nicht konfiguriert (Dev-Modus).")
            return

        release = _check_for_update(GITHUB_REPO)
        if release is None:
            st.caption("Update-Server nicht erreichbar.")
            return

        if not is_newer(release.tag, current_version()):
            st.caption("Auf neuestem Stand ✓")
            return

        st.markdown(f"**Neue Version verfügbar: {release.tag}**")
        if release.body:
            with st.expander("Was ist neu?"):
                st.markdown(release.body)

        if st.session_state.get("update_staged"):
            st.success("Update geladen. Bitte App neu starten.")
            return

        if not st.button("Aktualisieren", type="primary", use_container_width=True):
            return

        progress = st.progress(0.0, text="Lade Update...")
        update_exc: Exception | None = None
        try:
            download_update(release, progress=lambda p: progress.progress(p))
        except Exception as e:
            update_exc = e
        progress.empty()

        if update_exc is None:
            st.session_state.update_staged = True
            st.success("Update geladen. Bitte App neu starten.")
            st.rerun()
        else:
            report_error(
                "Update-Download fehlgeschlagen",
                details=(
                    f"Aktuelle Version: {current_version()}\n"
                    f"Ziel-Version: {release.tag}\n"
                    f"Asset-URL: {release.asset_url}"
                ),
                exc=update_exc,
            )
