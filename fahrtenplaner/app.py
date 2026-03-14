"""Erhebungsfahrten-Planer – Streamlit Web App."""

from __future__ import annotations

import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st

# Modell-Importe
sys.path.insert(0, str(Path(__file__).parent))
from models import Tour, DayPlan, ChainLink
from myres_client import MyRESClient, load_tours_from_excel
from optimizer import optimize_day

# Pfad zur lokalen Demo-Excel (für Entwicklung, falls MyRES nicht erreichbar)
_DEMO_EXCEL = Path(__file__).parent.parent / "Freie_Touren_BB_MV_01-03_April_2026.xlsx"

# ---------------------------------------------------------------------------
# Page Config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Erhebungsfahrten-Planer",
    page_icon="🚂",
    layout="wide",
)

# ---------------------------------------------------------------------------
# Styles
# ---------------------------------------------------------------------------

st.markdown("""
<style>
    .tour-mi { background-color: #D6E4F0; padding: 2px 8px; border-radius: 4px; }
    .tour-do { background-color: #E2EFDA; padding: 2px 8px; border-radius: 4px; }
    .tour-fr { background-color: #FCE4D6; padding: 2px 8px; border-radius: 4px; }
    .warning-box {
        background-color: #FFF3CD; border: 1px solid #FFC107;
        padding: 8px 12px; border-radius: 6px; margin: 4px 0;
    }
    .sev-box {
        background-color: #F8D7DA; border: 1px solid #DC3545;
        padding: 8px 12px; border-radius: 6px; margin: 4px 0;
    }
    .result-metric {
        font-size: 2rem; font-weight: bold; color: #1f77b4;
    }
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Session State
# ---------------------------------------------------------------------------

if "tours" not in st.session_state:
    st.session_state.tours = []
if "myres_client" not in st.session_state:
    st.session_state.myres_client = None


# ---------------------------------------------------------------------------
# Sidebar – MyRES Login & Touren laden
# ---------------------------------------------------------------------------

st.sidebar.title("MyRES 3")

username = st.sidebar.text_input("Username")
password = st.sidebar.text_input("Passwort", type="password")

st.sidebar.markdown("### Filter")
states = st.sidebar.multiselect(
    "Bundesländer",
    ["Brandenburg", "Mecklenburg-Vorpommern", "Sachsen", "Sachsen-Anhalt",
     "Thüringen", "Berlin", "Niedersachsen", "Schleswig-Holstein"],
    default=["Brandenburg", "Mecklenburg-Vorpommern"],
)

selected_date = st.sidebar.date_input("Datum", value=date(2026, 4, 1))

st.sidebar.markdown("### Bahnhöfe")
home_station = st.sidebar.text_input(
    "Abfahrtsbahnhof",
    value="Prenzlau",
    help="Von wo aus startet ihr morgens?",
)
same_station = st.sidebar.checkbox("Ankunft = Abfahrt", value=True)
if same_station:
    dest_station = home_station
else:
    dest_station = st.sidebar.text_input(
        "Ankunftsbahnhof",
        value="Stralsund",
        help="Wo wollt ihr abends ankommen?",
    )

if st.sidebar.button("Touren laden", type="primary", use_container_width=True):
    if not username or not password:
        st.sidebar.error("Username und Passwort eingeben!")
    else:
        with st.sidebar.status("Verbinde mit MyRES...") as status:
            client = MyRESClient()
            if client.login(username, password):
                status.update(label="Login OK – lade Touren...")
                tours = client.fetch_free_tours(states, selected_date, selected_date)
                st.session_state.tours = tours
                st.session_state.myres_client = client
                if tours:
                    status.update(label=f"{len(tours)} Touren geladen!", state="complete")
                else:
                    status.update(label="Login OK, aber keine Touren gefunden", state="complete")
                    st.sidebar.warning("Keine Touren im gewählten Zeitraum. Filter anpassen?")
            else:
                status.update(label="Login fehlgeschlagen", state="error")
                st.sidebar.error(f"Login-Fehler: {client.last_error}")

# Demo-Daten (für Entwicklung / falls MyRES nicht erreichbar)
if not st.session_state.tours and _DEMO_EXCEL.exists():
    st.sidebar.divider()
    if st.sidebar.button("Demo-Daten laden (BB+MV April)", use_container_width=True):
        st.session_state.tours = load_tours_from_excel(_DEMO_EXCEL)
        st.rerun()

# Tour-Statistiken in Sidebar
tours = st.session_state.tours
if tours:
    st.sidebar.divider()
    st.sidebar.metric("Geladene Touren", len(tours))
    dates = sorted(set(t.date for t in tours))
    st.sidebar.metric("Tage", len(dates))
    total_euros = sum(t.euros for t in tours)
    st.sidebar.metric("Gesamtwert", f"{total_euros:.0f} €")


# ---------------------------------------------------------------------------
# Hauptbereich
# ---------------------------------------------------------------------------

st.title("Erhebungsfahrten-Planer")
st.caption("Optimale Tourenketten für DB-Zugzählungen berechnen")

if not tours:
    st.info(
        "Lade zuerst Touren über die Sidebar (MyRES-Login)."
    )
    st.stop()


# ---------------------------------------------------------------------------
# Render-Hilfsfunktionen (müssen vor den Tabs definiert sein)
# ---------------------------------------------------------------------------


def _render_result(plan: DayPlan):
    """Rendert das Optimierungsergebnis."""

    # Kennzahlen
    st.success(f"Optimale Route gefunden!")
    m1, m2, m3 = st.columns(3)
    m1.metric("Gesamtverdienst", f"{plan.total_euros:.2f} €")
    m2.metric("Anzahl Touren", plan.num_tours)
    m3.metric("Zeitraum", plan.time_range)

    # Warnungen
    for warning in plan.warnings:
        st.warning(warning)

    st.divider()

    # Tagesplan
    st.subheader("Tagesplan")

    for i, link in enumerate(plan.chain):
        if link.type == "anreise":
            _render_connection_block("🚉 Anreise", link, "blue")
        elif link.type == "tour":
            _render_tour_block(link)
        elif link.type == "transfer":
            _render_connection_block("🔄 Transfer", link, "gray")
        elif link.type == "rückreise":
            _render_connection_block("🏠 Rückreise", link, "green")

    # Zusammenfassung als Tabelle
    st.divider()
    st.subheader("Zusammenfassung")

    summary_rows = []
    for link in plan.chain:
        if link.type == "tour" and link.tour:
            t = link.tour
            summary_rows.append({
                "Tour-Nr": t.tour_nr,
                "Ab": t.departure_time.strftime("%H:%M"),
                "Start": t.departure_station,
                "An": t.arrival_time.strftime("%H:%M"),
                "Ziel": t.arrival_station,
                "Fahrten": t.num_rides,
                "Euro": f"{t.euros:.2f} €",
            })

    if summary_rows:
        st.dataframe(
            pd.DataFrame(summary_rows),
            use_container_width=True,
            hide_index=True,
        )


def _render_tour_block(link: ChainLink):
    """Rendert einen Tour-Block."""
    tour = link.tour
    if not tour:
        return

    day_colors = {"Mi": "#D6E4F0", "Do": "#E2EFDA", "Fr": "#FCE4D6"}
    bg = day_colors.get(tour.day_name, "#F0F0F0")

    st.markdown(
        f"""
        <div style="background-color: {bg}; color: #1a1a1a; padding: 12px 16px;
                    border-radius: 8px; margin: 8px 0; border-left: 4px solid #1f77b4;">
            <strong>Tour {tour.tour_nr}</strong> — <strong>{tour.euros:.2f} €</strong>
            <br>
            {tour.departure_time:%H:%M} {tour.departure_station}
            → {tour.arrival_time:%H:%M} {tour.arrival_station}
            <br>
            <small style="color: #444;">{tour.num_rides} Fahrt(en) · {tour.duration_str} · {tour.points} Pkt</small>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_connection_block(title: str, link: ChainLink, color: str):
    """Rendert einen Verbindungs-Block (Anreise/Transfer/Rückreise)."""
    conn = link.connection
    if not conn or not conn.legs:
        return

    border_colors = {"blue": "#1f77b4", "green": "#2ca02c", "gray": "#999"}
    bc = border_colors.get(color, "#999")

    warning_html = ""
    if link.warning:
        if "Schienenersatzverkehr" in link.warning:
            warning_html = f'<div class="sev-box">⚠️ {link.warning}</div>'
        else:
            warning_html = f'<div class="warning-box">⚠️ {link.warning}</div>'

    legs_html = ""
    for leg in conn.legs:
        sev_marker = " 🚌 SEV" if leg.is_replacement_service else ""
        legs_html += (
            f"<div style='margin-left: 12px; padding: 2px 0;'>"
            f"<code>{leg.departure_time:%H:%M}</code> {leg.departure_station} "
            f"→ <code>{leg.arrival_time:%H:%M}</code> {leg.arrival_station} "
            f"<small>({leg.line}{sev_marker})</small>"
            f"</div>"
        )

    with st.expander(f"{title} — {conn.duration_str}, {conn.transfers} Umstieg(e)"):
        st.markdown(f"{warning_html}{legs_html}", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Tab 1: Touren-Übersicht
# ---------------------------------------------------------------------------

tab_overview, tab_optimize = st.tabs(["Touren-Übersicht", "Optimierung"])

with tab_overview:
    st.subheader("Verfügbare Touren")

    # Zu DataFrame konvertieren
    df = pd.DataFrame([
        {
            "Tour-Nr": t.tour_nr,
            "Prio": t.priority,
            "Tag": t.day_name,
            "Datum": t.date.strftime("%d.%m.%Y"),
            "Ab": t.departure_time.strftime("%H:%M"),
            "Startbahnhof": t.departure_station,
            "An": t.arrival_time.strftime("%H:%M"),
            "Zielbahnhof": t.arrival_station,
            "Fahrten": t.num_rides,
            "Punkte": t.points,
            "Dauer": t.duration_str,
            "Euro": t.euros,
        }
        for t in tours
    ])

    # Filter
    col_f1, col_f2 = st.columns(2)
    with col_f1:
        min_euro = st.number_input("Min. Euro", value=0.0, step=5.0)
    with col_f2:
        station_filter = st.text_input("Station (enthält)")

    filtered = df
    if min_euro > 0:
        filtered = filtered[filtered["Euro"] >= min_euro]
    if station_filter:
        mask = (
            filtered["Startbahnhof"].str.contains(station_filter, case=False, na=False)
            | filtered["Zielbahnhof"].str.contains(station_filter, case=False, na=False)
        )
        filtered = filtered[mask]

    st.dataframe(
        filtered.style.format({"Euro": "{:.2f} €"}),
        use_container_width=True,
        height=min(600, 35 * len(filtered) + 38),
        hide_index=True,
    )

    st.caption(f"{len(filtered)} von {len(df)} Touren angezeigt")


# ---------------------------------------------------------------------------
# Tab 2: Optimierung
# ---------------------------------------------------------------------------

with tab_optimize:
    st.subheader("Optimale Tourenkette berechnen")

    day_tours = [t for t in tours if t.date == selected_date]

    st.caption(f"{len(day_tours)} Touren am {selected_date.strftime('%d.%m.%Y')} verfügbar")
    if same_station:
        st.info(f"Route: **{home_station}** → Touren → **{home_station}**")
    else:
        st.info(f"Route: **{home_station}** → Touren → **{dest_station}**")

    col1, col2, col3 = st.columns(3)
    with col1:
        dep_time = st.time_input("Früheste Abfahrt", value=time(4, 0))
    with col2:
        ret_time = st.time_input("Späteste Rückkehr", value=time(23, 59))
    with col3:
        max_gap_minutes = st.number_input(
            "Max. Pause zwischen Touren (Min.)",
            min_value=10,
            max_value=1440,
            value=60,
            step=10,
            help="Maximale Zeit zwischen Ende einer Tour und Beginn der nächsten (inkl. Leerfahrt)",
        )

    # Optimierung starten
    st.divider()

    if st.button("Optimale Route berechnen", type="primary", use_container_width=True):
        if not day_tours:
            st.error("Keine Touren für diesen Tag verfügbar!")
        elif not home_station:
            st.error("Bitte Abfahrtsbahnhof eingeben!")
        else:
            earliest = datetime.combine(selected_date, dep_time)
            latest = datetime.combine(selected_date, ret_time)
            # Wenn Rückkehr vor Abfahrt → nächster Tag
            if latest <= earliest:
                latest += timedelta(days=1)

            progress_bar = st.progress(0, text="Starte Optimierung...")
            log_messages: list[str] = []

            def progress_cb(pct: float, msg: str):
                progress_bar.progress(min(pct, 1.0), text=msg)
                log_messages.append(msg)

            plan = optimize_day(
                tours=day_tours,
                home_station=home_station,
                dest_station=dest_station,
                earliest_departure=earliest,
                latest_return=latest,
                progress_callback=progress_cb,
                max_transfer_gap_hours=max_gap_minutes / 60,
            )

            progress_bar.empty()

            # Optimierungsdetails anzeigen
            with st.expander("Optimierungsdetails"):
                for msg in log_messages:
                    st.text(msg)

            if plan.num_tours == 0:
                st.warning(
                    "Keine gültige Tourenkette gefunden. Versuche:\n"
                    "- Früheste Abfahrt vorverlegen\n"
                    "- Späteste Rückkehr nach hinten schieben\n"
                    "- Anderen Start-/Zielbahnhof wählen"
                )
            else:
                _render_result(plan)


# ---------------------------------------------------------------------------
# Footer
# ---------------------------------------------------------------------------

st.sidebar.divider()
st.sidebar.caption("Erhebungsfahrten-Planer v1.0")
st.sidebar.caption("DB-API: v6.db.transport.rest (kostenlos)")
