"""Session-state initialization. Idempotent — safe to call on every rerun."""

from __future__ import annotations

import streamlit as st


def init_session_state() -> None:
    if "tours" not in st.session_state:
        st.session_state.tours = []
    if "myres_client" not in st.session_state:
        st.session_state.myres_client = None

    myres_defaults = st.secrets.get("myres", {})
    if "myres_username" not in st.session_state:
        st.session_state.myres_username = str(myres_defaults.get("username", ""))
    if "myres_password" not in st.session_state:
        st.session_state.myres_password = str(myres_defaults.get("password", ""))

    if "last_plan" not in st.session_state:
        st.session_state.last_plan = None
    if "last_plan_log" not in st.session_state:
        st.session_state.last_plan_log = []
