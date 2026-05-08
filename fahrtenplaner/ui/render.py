"""DayPlan result rendering: metrics, map (toggleable), chain links, summary table."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from models import ChainLink, DayPlan
from .map import render_route_map


def _render_tour_block(link: ChainLink) -> None:
    """Rendert einen Tour-Block im Bahnamt-Stil."""
    tour = link.tour
    if not tour:
        return

    day_class = {"Mi": "day-mi", "Do": "day-do", "Fr": "day-fr"}.get(tour.day_name, "")
    price = f"{tour.euros:.2f}".replace(".", ",")

    st.markdown(
        f"""
        <div class="tour-card {day_class}">
          <div class="head">
            <span class="nr">Tour № {tour.tour_nr} · {tour.day_name}</span>
            <span class="price">{price}&nbsp;€</span>
          </div>
          <div class="route">
            <span class="time">{tour.departure_time:%H:%M}</span>
            <span class="station">&nbsp;{tour.departure_station}</span>
            <span class="arrow">→</span>
            <span class="time">{tour.arrival_time:%H:%M}</span>
            <span class="station">&nbsp;{tour.arrival_station}</span>
          </div>
          <div class="meta">{tour.num_rides} Fahrt(en) · {tour.duration_str} h · {tour.points} Pkt</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_connection_block(title: str, link: ChainLink) -> None:
    """Rendert einen Verbindungs-Block (Anreise/Transfer/Rückreise)."""
    conn = link.connection
    if not conn or not conn.legs:
        return

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


def _render_summary_table(plan: DayPlan) -> None:
    rows = []
    for link in plan.chain:
        if link.type == "tour" and link.tour:
            t = link.tour
            rows.append({
                "Tour-Nr": t.tour_nr,
                "Ab": t.departure_time.strftime("%H:%M"),
                "Start": t.departure_station,
                "An": t.arrival_time.strftime("%H:%M"),
                "Ziel": t.arrival_station,
                "Fahrten": t.num_rides,
                "Euro": f"{t.euros:.2f} €",
            })
    if rows:
        st.dataframe(
            pd.DataFrame(rows),
            use_container_width=True,
            hide_index=True,
        )


def render_result(plan: DayPlan) -> None:
    """Render a successful optimization result: metrics, optional map, chain, summary."""
    st.success("Optimale Route gefunden!")

    m1, m2, m3 = st.columns(3)
    m1.metric("Gesamtverdienst", f"{plan.total_euros:.2f} €")
    m2.metric("Anzahl Touren", plan.num_tours)
    m3.metric("Zeitraum", plan.time_range)

    for warning in plan.warnings:
        st.warning(warning)

    st.divider()

    if st.toggle(
        "Tagesroute auf Karte anzeigen",
        value=False,
        key="show_route_map",
        help="Zeigt Anreise (grau), Touren (rot) und Rückreise (grau) auf einer Karte.",
    ):
        render_route_map(plan)

    st.divider()

    st.subheader("Tagesplan")
    for link in plan.chain:
        if link.type == "anreise":
            _render_connection_block("🚉 Anreise", link)
        elif link.type == "tour":
            _render_tour_block(link)
        elif link.type == "transfer":
            _render_connection_block("🔄 Transfer", link)
        elif link.type == "rückreise":
            _render_connection_block("🏠 Rückreise", link)

    st.divider()
    st.subheader("Zusammenfassung")
    _render_summary_table(plan)
