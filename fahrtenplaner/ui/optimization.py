"""Plan-context strip + optimization panel + result display + tour browser.

This is the main pane below the hero. Single linear flow:
    plan-strip → section heading → inputs → button → result → tour browser
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pandas as pd
import streamlit as st

from models import DayPlan, OptimizationResult, Tour
from optimizer import optimize_with_modes
from .errors import report_error
from .feedback import render_feedback_button
from .render import render_result
from .sidebar import SidebarContext


_WEEKDAYS_DE = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]


def _parse_hhmm(s: str, fallback: time) -> tuple[time, bool]:
    """Parse 'HH:MM' (forgiving: '4:00', '4:5', '04:05'). Returns (time, is_valid)."""
    s = (s or "").strip()
    if ":" in s:
        try:
            h_str, m_str = s.split(":", 1)
            h, mi = int(h_str), int(m_str)
            if 0 <= h <= 23 and 0 <= mi <= 59:
                return time(h, mi), True
        except (ValueError, IndexError):
            pass
    return fallback, False


def _render_plan_strip(
    selected_date: date, home: str, dest: str, same_station: bool, day_tour_count: int,
) -> None:
    day_label = f"{_WEEKDAYS_DE[selected_date.weekday()]} · {selected_date.strftime('%d.%m.%Y')}"
    route_html = (
        f'<span class="station">{home}</span>'
        f'<span class="arrow">→</span>'
        f'<span class="station">{home if same_station else dest}</span>'
    )
    st.markdown(
        f"""
        <div class="plan-strip">
          <div class="item">
            <span class="label">Datum</span>
            <span class="value">{day_label}</span>
          </div>
          <div class="sep">·</div>
          <div class="item">
            <span class="label">Touren am Tag</span>
            <span class="value">{day_tour_count}</span>
          </div>
          <div class="sep">·</div>
          <div class="item">
            <span class="label">Route</span>
            <span class="value">{route_html}</span>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_section_heading() -> None:
    st.markdown(
        """
        <div class="section-head">
          <h2>Optimale Tourenkette berechnen</h2>
          <p class="lede">Anreise · Touren · Transfers · Rückreise – als ein durchgehender Tagesplan.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_param_inputs() -> tuple[time, time, int]:
    """Time-window + transfer cap. Auto-Anfahrt lives in the sidebar's
    Auto-Anfahrt panel — keeping the main-pane inputs to the three knobs
    that always apply to every chain."""
    col1, col2, col3 = st.columns(3)
    with col1:
        dep_str = st.text_input(
            "Früheste Abfahrt", value="04:00", placeholder="HH:MM",
            help="Frühester Start zuhause — Format HH:MM, z. B. 04:00",
        )
        dep_time, dep_ok = _parse_hhmm(dep_str, time(4, 0))
        if not dep_ok and dep_str.strip():
            st.caption(":red[Bitte HH:MM eingeben — z. B. 04:00]")
    with col2:
        ret_str = st.text_input(
            "Späteste Rückkehr", value="23:59", placeholder="HH:MM",
            help="Spätestes Eintreffen am Zielbahnhof — Format HH:MM, z. B. 23:59",
        )
        ret_time, ret_ok = _parse_hhmm(ret_str, time(23, 59))
        if not ret_ok and ret_str.strip():
            st.caption(":red[Bitte HH:MM eingeben — z. B. 23:59]")
    with col3:
        max_gap_minutes = st.number_input(
            "Max. Pause zwischen Touren (Min.)",
            min_value=10, max_value=240, value=60, step=10,
            help="Maximale Zeit zwischen Ende einer Tour und Beginn der nächsten (inkl. Leerfahrt)",
        )
    return dep_time, ret_time, int(max_gap_minutes)


def _run_optimization(
    day_tours: list[Tour], ctx: SidebarContext,
    dep_time: time, ret_time: time, max_gap_minutes: int, max_car_minutes: int,
) -> None:
    earliest = datetime.combine(ctx.selected_date, dep_time)
    latest = datetime.combine(ctx.selected_date, ret_time)
    if latest <= earliest:
        latest += timedelta(days=1)

    progress_bar = st.progress(0, text="Starte Optimierung...")
    log_messages: list[str] = []

    def progress_cb(pct: float, msg: str) -> None:
        progress_bar.progress(min(pct, 1.0), text=msg)
        log_messages.append(msg)

    opt_exc: Exception | None = None
    try:
        result = optimize_with_modes(
            tours=day_tours,
            home_station=ctx.home_station,
            dest_station=ctx.dest_station,
            earliest_departure=earliest,
            latest_return=latest,
            max_car_minutes=max_car_minutes,
            fuel_consumption=st.session_state.fuel_consumption,
            fuel_price=st.session_state.fuel_price,
            fuel_refund_per_km=st.session_state.fuel_refund_per_km,
            progress_callback=progress_cb,
            max_transfer_gap_hours=max_gap_minutes / 60,
        )
    except Exception as e:
        result = OptimizationResult(winner=DayPlan(), alternative=None)
        opt_exc = e

    progress_bar.empty()
    st.session_state.last_plan = result
    st.session_state.last_plan_log = log_messages
    st.session_state.last_plan_inputs = {
        "date":                     ctx.selected_date.isoformat(),
        "home_station":             ctx.home_station,
        "dest_station":             ctx.dest_station,
        "same_station":             ctx.same_station,
        "earliest_departure":       dep_time.strftime("%H:%M"),
        "latest_return":            ret_time.strftime("%H:%M"),
        "max_transfer_gap_minutes": max_gap_minutes,
        "max_car_minutes":          max_car_minutes,
        "fuel_consumption":         float(st.session_state.fuel_consumption),
        "fuel_price":               float(st.session_state.fuel_price),
        "fuel_refund_per_km":       float(st.session_state.fuel_refund_per_km),
        "selected_bundeslaender":   st.session_state.get("myres_states", []),
    }

    if opt_exc is not None:
        report_error(
            "Optimierung fehlgeschlagen",
            details=(
                f"Datum: {ctx.selected_date.strftime('%d.%m.%Y')}\n"
                f"Route: {ctx.home_station} → "
                f"{ctx.home_station if ctx.same_station else ctx.dest_station}\n"
                f"Touren am Tag: {len(day_tours)}\n"
                f"Fenster: {dep_time:%H:%M}–{ret_time:%H:%M}, "
                f"max. Pause: {max_gap_minutes} Min, max. Auto: {max_car_minutes} Min\n"
                f"Letzter Schritt: {(log_messages[-1] if log_messages else '—')}"
            ),
            exc=opt_exc,
        )


def _render_empty_state(day_tours: list[Tour], ctx: SidebarContext) -> None:
    if not day_tours:
        date_label = ctx.selected_date.strftime("%d.%m.%Y")
        st.markdown(
            f"""
            <div class="empty-result">
              <strong>Keine Touren am {date_label}.</strong>
              <span class="hint">Wähle ein anderes Datum oder lade die Touren neu.</span>
            </div>
            """,
            unsafe_allow_html=True,
        )
    elif not ctx.home_station:
        st.markdown(
            """
            <div class="empty-result">
              <strong>Abfahrtsbahnhof fehlt.</strong>
              <span class="hint">Trage den Bahnhof in der Sidebar ein.</span>
            </div>
            """,
            unsafe_allow_html=True,
        )
    else:
        weekday = _WEEKDAYS_DE[ctx.selected_date.weekday()]
        date_label = ctx.selected_date.strftime("%d.%m.%Y")
        st.markdown(
            f"""
            <div class="empty-result">
              <strong>{len(day_tours)} Touren am {weekday}, {date_label} bereit.</strong>
              <span class="hint">
                Klicke oben <strong>Optimale Route berechnen</strong>
                für die einträglichste Tourenkette ab {ctx.home_station}.
              </span>
            </div>
            """,
            unsafe_allow_html=True,
        )


def _render_no_chain_warning() -> None:
    st.warning(
        "Keine gültige Tourenkette gefunden. Versuche:\n"
        "- Früheste Abfahrt vorverlegen\n"
        "- Späteste Rückkehr nach hinten schieben\n"
        "- Anderen Start-/Zielbahnhof wählen"
    )


def _render_optimization_log() -> None:
    log = st.session_state.get("last_plan_log") or []
    if log:
        with st.expander("Optimierungsdetails"):
            for msg in log:
                st.text(msg)


def _render_tour_browser(tours: list[Tour], day_count: int, expanded: bool) -> None:
    label = (
        f"Alle verfügbaren Touren  ·  {day_count} am Tag  ·  "
        f"{len(tours)} insgesamt"
    )
    with st.expander(label, expanded=expanded):
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


def render_optimization_section(tours: list[Tour], ctx: SidebarContext) -> None:
    """Top-level main-pane rendering: plan-strip, optimization panel, result, tour browser."""
    day_tours = [t for t in tours if t.date == ctx.selected_date]

    # Confirmation banner after a successful tour load. We pop the flags so
    # they only trigger once. The same flag drives the auto-expanded tour
    # browser at the bottom of the section, so we capture its value first.
    just_loaded = bool(st.session_state.pop("tours_just_loaded", False))
    discarded = bool(st.session_state.pop("tours_reload_discarded", False))
    if just_loaded:
        weekday = _WEEKDAYS_DE[ctx.selected_date.weekday()]
        date_label = ctx.selected_date.strftime("%d.%m.%Y")
        st.success(
            f"{len(tours)} Touren geladen — davon {len(day_tours)} am "
            f"{weekday}, {date_label}."
        )
        if discarded:
            st.caption(
                "Vorherige Optimierung verworfen — Daten frisch geladen."
            )

    _render_plan_strip(
        ctx.selected_date, ctx.home_station, ctx.dest_station, ctx.same_station,
        len(day_tours),
    )
    _render_section_heading()
    dep_time, ret_time, max_gap_minutes = _render_param_inputs()

    if st.button(
        "Optimale Route berechnen",
        type="primary",
        use_container_width=True,
        disabled=not day_tours or not ctx.home_station,
    ):
        _run_optimization(
            day_tours, ctx, dep_time, ret_time, max_gap_minutes, ctx.max_car_minutes,
        )

    result = st.session_state.last_plan  # now Optional[OptimizationResult]
    if result is None:
        _render_empty_state(day_tours, ctx)
    elif result.winner.num_tours == 0:
        _render_no_chain_warning()
        _render_optimization_log()
    else:
        render_result(result)
        _render_optimization_log()
        render_feedback_button(
            result, ctx, tours,
            inputs=st.session_state.get("last_plan_inputs"),
        )

    _render_tour_browser(tours, day_count=len(day_tours), expanded=just_loaded)
