"""DayPlan result rendering: metrics, map (toggleable), chain links, summary table."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

import pandas as pd
import streamlit as st

from models import ChainLink, DayPlan, OptimizationResult
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


def _render_car_leg_block(link: ChainLink) -> None:
    """Single-line block for car-mode anreise/rückreise drives."""
    leg = link.car_leg
    if not leg:
        return
    icon = "🚗"
    label = link.label  # "Auto-Anfahrt" or "Auto-Rückfahrt"
    cost_str = f"{leg.cost:.2f} €".replace(".", ",")
    st.markdown(
        f"""
        <div class="auto-leg">
          <span class="icon">{icon}</span>
          <span class="label">{label}</span>
          <span class="meta">{leg.minutes} min · {leg.km:.0f} km · {cost_str} Sprit</span>
          <span class="dest">→ {leg.to_station}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )


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


def _render_winner_metrics(plan: DayPlan, fuel_consumption: float, fuel_price: float) -> None:
    """Three-line metric stack with optional cost breakdown for car-mode plans."""
    if plan.has_car_legs:
        st.success(f"Optimaler Auto-Plan · {plan.net_euros:.2f} € netto".replace(".", ","))
    else:
        st.success(f"Optimaler Transit-Plan · {plan.total_euros:.2f} €".replace(".", ","))

    m1, m2, m3 = st.columns(3)
    m1.metric(
        "Gesamtverdienst",
        f"{plan.net_euros:.2f} € netto".replace(".", ","),
        delta=f"−{plan.total_costs:.2f} € Sprit".replace(".", ",") if plan.has_car_legs else None,
    )
    m2.metric("Anzahl Touren", plan.num_tours)
    m3.metric("Zeitraum", plan.time_range)

    if plan.has_car_legs:
        total_km = sum(link.car_leg.km for link in plan.chain if link.car_leg is not None)
        st.caption(
            f"Verdienst {plan.total_euros:.2f} € − Sprit {plan.total_costs:.2f} € "
            f"({total_km:.0f} km · {fuel_consumption:.1f} l/100km · {fuel_price:.2f} €/l) "
            f"= {plan.net_euros:.2f} € netto".replace(".", ",")
        )


def render_result(result: OptimizationResult) -> None:
    """Render winner card + optional alternative expander."""
    fuel_consumption = float(st.session_state.get("fuel_consumption", 7.0))
    fuel_price = float(st.session_state.get("fuel_price", 1.79))
    latest_return_target = result.latest_return_target

    _render_plan_full(result.winner, fuel_consumption, fuel_price, map_key="winner",
                      latest_return_target=latest_return_target)

    if not result.has_alternative:
        return

    alt = result.alternative
    label = (
        f"▸ Alternative anzeigen · "
        f"{'Auto' if alt.has_car_legs else 'Transit'} "
        f"{alt.net_euros:.2f} € netto ({alt.num_tours} Touren)"
    ).replace(".", ",")
    with st.expander(label, expanded=False):
        _render_plan_full(alt, fuel_consumption, fuel_price, map_key="alternative",
                          latest_return_target=latest_return_target)


def _chain_end_time(plan: DayPlan) -> Optional[datetime]:
    """Latest arrival-at-home time. For car_inbound, adds drive_min to the prior link's
    arrival so the user sees the *actual* time they get home, not just when their last
    transit/tour leg ended."""
    if not plan.chain:
        return None
    last = plan.chain[-1]
    if last.connection and last.connection.arrival_time:
        return last.connection.arrival_time
    if last.tour:
        return last.tour.arrival_dt
    # car_inbound has no internal datetime — its arrival is prior_link.arrival + drive_min
    drive_min = last.car_leg.minutes if last.car_leg else 0
    for link in reversed(plan.chain[:-1]):
        if link.connection and link.connection.arrival_time:
            return link.connection.arrival_time + timedelta(minutes=drive_min)
        if link.tour:
            return link.tour.arrival_dt + timedelta(minutes=drive_min)
    return None


def _render_plan_full(
    plan: DayPlan,
    fuel_consumption: float,
    fuel_price: float,
    map_key: str = "winner",
    latest_return_target: Optional[datetime] = None,
) -> None:
    """Full plan rendering: metrics + warnings + map toggle + chain + summary."""
    _render_winner_metrics(plan, fuel_consumption, fuel_price)

    # Overshoot caption: show when chain ends after the user's preferred return time
    end_dt = _chain_end_time(plan)
    if latest_return_target is not None and end_dt is not None:
        if end_dt > latest_return_target + timedelta(minutes=1):
            delta = end_dt - latest_return_target
            delta_min = int(delta.total_seconds() / 60)
            h, m = divmod(delta_min, 60)
            delta_str = f"{h}h{m:02d}" if h > 0 else f"{m} Min."
            st.caption(
                f"⚠ Rückkehr {end_dt.strftime('%H:%M')} "
                f"({delta_str} nach {latest_return_target.strftime('%H:%M')} Wunschzeit)"
            )

    for warning in plan.warnings:
        st.warning(warning)

    st.divider()

    if st.toggle(
        "Tagesroute auf Karte anzeigen",
        value=False,
        key=f"show_route_map_{map_key}",   # stable across reruns; unique per plan slot
        help="Zeigt Anreise (grau), Touren (rot) und Rückreise (grau) auf einer Karte.",
    ):
        render_route_map(plan)

    st.divider()

    st.subheader("Tagesplan")
    for link in plan.chain:
        if link.type == "outbound":
            _render_connection_block("🚉 Anreise", link)
        elif link.type == "inbound":
            title = "🚆 Rückfahrt zum Auto" if plan.has_car_legs else "🏠 Rückreise"
            _render_connection_block(title, link)
        elif link.type == "transfer":
            _render_connection_block("🔄 Transfer", link)
        elif link.type == "tour":
            _render_tour_block(link)
        elif link.type in ("car_outbound", "car_inbound"):
            _render_car_leg_block(link)

    st.divider()
    st.subheader("Zusammenfassung")
    _render_summary_table(plan)
