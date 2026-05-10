"""Session-state initialization. Idempotent — safe to call on every rerun."""

from __future__ import annotations

from datetime import date

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

    if "fuel_consumption" not in st.session_state:
        st.session_state.fuel_consumption = 7.0
    if "fuel_price" not in st.session_state:
        st.session_state.fuel_price = 1.79
    if "max_car_minutes" not in st.session_state:
        st.session_state.max_car_minutes = 0

    # Datum picker default. Seeded here (rather than via `value=` on the widget)
    # so the demo loader's deferred update doesn't trigger Streamlit's
    # "default value but also session_state" warning.
    if "datum_input" not in st.session_state:
        st.session_state.datum_input = date.today()
