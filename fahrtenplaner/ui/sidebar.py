"""All sidebar widgets: MyRES login, filters, date, stations, load button,
demo-data, tour stats, update panel, debug panel, footer.

Returns a SidebarContext object the main pane uses to render the optimization.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import streamlit as st

from myres_client import MyRESClient, load_tours_from_excel
from updater import current_version
from .errors import report_error
from .update_panel import render_update_panel


_BUNDESLAENDER = [
    "Brandenburg", "Mecklenburg-Vorpommern", "Sachsen", "Sachsen-Anhalt",
    "Thüringen", "Berlin", "Niedersachsen", "Schleswig-Holstein",
]

_DEMO_EXCEL = (
    Path(__file__).resolve().parent.parent.parent / "Freie_Touren_BB_MV_01-03_April_2026.xlsx"
)


@dataclass
class SidebarContext:
    """User-selected configuration produced by the sidebar; consumed by the main pane."""
    selected_date: date
    home_station: str
    dest_station: str
    same_station: bool


def _render_credentials_section() -> tuple[str, str]:
    with st.sidebar.expander("MyRES Zugangsdaten"):
        username = st.text_input(
            "Username", value=st.session_state.myres_username, key="myres_user_input",
        )
        password = st.text_input(
            "Passwort", value=st.session_state.myres_password,
            type="password", key="myres_pw_input",
        )
        st.session_state.myres_username = username
        st.session_state.myres_password = password
    return username, password


def _render_filters() -> list[str]:
    st.sidebar.markdown("### Filter")
    return st.sidebar.multiselect(
        "Bundesländer",
        _BUNDESLAENDER,
        default=["Brandenburg", "Mecklenburg-Vorpommern"],
    )


def _render_stations() -> tuple[str, str, bool]:
    st.sidebar.markdown("### Bahnhöfe")
    home = st.sidebar.text_input(
        "Abfahrtsbahnhof", value="Prenzlau",
        help="Von wo aus startet ihr morgens?",
    )
    same = st.sidebar.checkbox("Ankunft = Abfahrt", value=True)
    if same:
        return home, home, True
    dest = st.sidebar.text_input(
        "Ankunftsbahnhof", value="Stralsund",
        help="Wo wollt ihr abends ankommen?",
    )
    return home, dest, False


def _handle_load_tours(
    username: str, password: str, states: list[str], selected_date: date,
) -> None:
    """Run the MyRES login + fetch flow, surface errors via the error dialog."""
    if not username or not password:
        st.sidebar.error("Zugangsdaten fehlen – klicke ⚙️ oben")
        return

    fetch_failed = False
    fetch_exc: Exception | None = None
    error_title = ""
    error_details = ""
    date_str = selected_date.strftime("%d.%m.%Y")

    with st.sidebar.status("Verbinde mit MyRES...") as status:
        client = MyRESClient()
        if not client.login(username, password):
            status.update(label="Login fehlgeschlagen", state="error")
            fetch_failed = True
            error_title = "Anmeldung bei MyRES fehlgeschlagen"
            error_details = (
                f"MyRES-Fehlermeldung: {client.last_error}\n"
                f"Benutzername: {username}\n"
                f"Bundesländer: {', '.join(states)}\n"
                f"Datum: {date_str}"
            )
        else:
            status.update(label="Login OK – lade Touren...")
            try:
                tours = client.fetch_free_tours(states, selected_date, selected_date)
            except Exception as e:
                tours = []
                fetch_failed = True
                fetch_exc = e
                error_title = "Touren-Abruf fehlgeschlagen (Ausnahme)"
                error_details = (
                    f"Bundesländer: {', '.join(states)}\n"
                    f"Datum: {date_str}"
                )

            st.session_state.tours = tours
            st.session_state.myres_client = client

            if tours:
                status.update(label=f"{len(tours)} Touren geladen!", state="complete")
            elif client.last_error:
                status.update(label="Touren-Abruf fehlgeschlagen", state="error")
                fetch_failed = True
                error_title = "Touren-Abruf fehlgeschlagen"
                error_details = (
                    f"MyRES-Fehlermeldung: {client.last_error}\n"
                    f"Bundesländer: {', '.join(states)}\n"
                    f"Datum: {date_str}"
                )
            elif not fetch_failed:
                status.update(label="Keine Touren gefunden", state="complete")
                st.sidebar.warning(
                    "Keine Touren im gewählten Zeitraum. Bundesländer / Datum anpassen?"
                )

    if fetch_failed:
        report_error(error_title, details=error_details, exc=fetch_exc)


def _render_demo_data_button() -> None:
    if st.session_state.tours or not _DEMO_EXCEL.exists():
        return
    st.sidebar.divider()
    if st.sidebar.button("Demo-Daten laden (BB+MV April)", use_container_width=True):
        st.session_state.tours = load_tours_from_excel(_DEMO_EXCEL)
        st.rerun()


def _render_tour_stats() -> None:
    tours = st.session_state.tours
    if not tours:
        return
    st.sidebar.divider()
    st.sidebar.metric("Geladene Touren", len(tours))
    dates = sorted({t.date for t in tours})
    st.sidebar.metric("Tage", len(dates))
    total_euros = sum(t.euros for t in tours)
    st.sidebar.metric("Gesamtwert", f"{total_euros:.0f} €")


def _render_debug_panel() -> None:
    with st.sidebar.expander("Debug / Diagnostik"):
        key = st.secrets.get("GOOGLE_MAPS_API_KEY", "")
        st.text(f"API Key: {'✅ loaded (' + str(len(key)) + ' chars)' if key else '❌ missing'}")

        if st.button("Google Maps testen"):
            try:
                from db_client import lookup_station
                result = lookup_station("Prenzlau")
                if result:
                    st.success(f"OK: {result['name']}")
                else:
                    st.error("Station nicht gefunden (None returned)")
            except Exception as e:
                st.exception(e)

        if st.button("MyRES erreichbar?"):
            try:
                from curl_cffi.requests import Session
                s = Session(impersonate="chrome", verify=False)
                resp = s.get("https://res.ivv-berlin.de", timeout=15)
                st.success(f"OK: HTTP {resp.status_code}")
                s.close()
            except Exception as e:
                st.exception(e)

        if st.button("MyRES IP-Test (WAF check)"):
            import subprocess
            try:
                result = subprocess.run(
                    ["curl", "-s", "-o", "/dev/null", "-w",
                     "%{http_code} %{time_connect}s connect, %{time_total}s total",
                     "--connect-timeout", "10", "-k",
                     "-H", "Host: res.ivv-berlin.de",
                     "https://193.111.43.2/"],
                    capture_output=True, text=True, timeout=15,
                )
                st.code(f"stdout: {result.stdout}\nstderr: {result.stderr}")
            except Exception as e:
                st.exception(e)

        import platform
        st.text(f"Python: {platform.python_version()}")
        st.text(f"Platform: {platform.platform()}")


def _render_footer() -> None:
    st.sidebar.markdown(
        f"""
        <div style="font-family:'Onest',sans-serif;font-size:0.78rem;
                    color:#8E8C87;margin-top:0.6rem;
                    border-top:1px solid #EFEEEA;padding-top:0.7rem;
                    line-height:1.5;">
            Fahrtenplaner
            <span style="font-family:'JetBrains Mono',monospace;font-size:0.7rem;
                         color:#0E0E0C;background:#F5F4F1;padding:0.05em 0.4em;
                         border-radius:4px;margin-left:0.3rem;">v{current_version()}</span>
            <br>
            <span style="font-size:0.72rem;">MyRES · Google Maps</span>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_sidebar() -> SidebarContext:
    """Render the entire sidebar; return the user-selected configuration."""
    username, password = _render_credentials_section()
    states = _render_filters()
    selected_date = st.sidebar.date_input("Datum", value=date.today())
    home_station, dest_station, same_station = _render_stations()

    if st.sidebar.button("Touren laden", type="primary", use_container_width=True):
        _handle_load_tours(username, password, states, selected_date)

    _render_demo_data_button()
    _render_tour_stats()

    st.sidebar.divider()
    render_update_panel()

    st.sidebar.divider()
    _render_debug_panel()

    _render_footer()

    return SidebarContext(
        selected_date=selected_date,
        home_station=home_station,
        dest_station=dest_station,
        same_station=same_station,
    )
