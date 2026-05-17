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
        st.session_state.fuel_price = 2.00
    if "fuel_refund_per_km" not in st.session_state:
        st.session_state.fuel_refund_per_km = 0.20
    if "max_car_minutes" not in st.session_state:
        st.session_state.max_car_minutes = 60

    # Datum picker default. Seeded here (rather than via `value=` on the widget)
    # so the demo loader's deferred update doesn't trigger Streamlit's
    # "default value but also session_state" warning.
    if "datum_input" not in st.session_state:
        st.session_state.datum_input = date.today()

    # Inputs captured at the moment of the last optimization (consumed by the
    # feedback dialog so it can describe what the user actually ran).
    if "last_plan_inputs" not in st.session_state:
        st.session_state.last_plan_inputs = None

    # Selected Bundesländer — persisted so the feedback payload can include them
    # (the sidebar's _render_data_source_panel writes here on every render).
    if "myres_states" not in st.session_state:
        st.session_state.myres_states = []

    # Feedback dialog state. Cleared after a successful submission.
    if "feedback_dialog_open" not in st.session_state:
        st.session_state.feedback_dialog_open = False
    if "feedback_text" not in st.session_state:
        st.session_state.feedback_text = ""
    if "feedback_type" not in st.session_state:
        st.session_state.feedback_type = None
    if "feedback_last_error" not in st.session_state:
        st.session_state.feedback_last_error = None
    if "feedback_last_issue_number" not in st.session_state:
        st.session_state.feedback_last_issue_number = None
