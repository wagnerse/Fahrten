"""Copyable error dialog so users can forward technical details to the developer."""

from __future__ import annotations

import traceback
from datetime import datetime

import streamlit as st

from updater import current_version


@st.dialog("Fehler – bitte an Sebastian senden", width="large")
def _show_error_dialog(title: str, details: str, timestamp: str) -> None:
    st.markdown(f"**{title}**")
    st.write(
        "Bitte schicke Sebastian den folgenden Text. "
        "Mit dem Symbol oben rechts im Feld kannst du alles kopieren."
    )
    body = (
        f"Fahrtenplaner v{current_version()}\n"
        f"Zeitpunkt: {timestamp}\n\n"
        f"{title}\n"
        f"--------------------------------\n"
        f"{details or '(keine weiteren Details)'}\n"
    )
    st.code(body, language="text")
    if st.button("Schließen", use_container_width=True):
        st.rerun()


def report_error(title: str, details: str = "", exc: BaseException | None = None) -> None:
    """Open a modal showing a copyable error report. Single funnel for all surface errors."""
    parts: list[str] = []
    if details:
        parts.append(details)
    if exc is not None:
        parts.append(f"{type(exc).__name__}: {exc}")
        tb = traceback.format_exc()
        if tb and tb.strip() and tb.strip() != "NoneType: None":
            parts.append(tb.rstrip())
    full = "\n\n".join(p for p in parts if p)
    ts = datetime.now().isoformat(timespec="seconds")
    _show_error_dialog(title, full, ts)
