"""DayPlan result rendering, Bahn-style row + collapsible details.

Each plan is rendered as a single horizontal row reminiscent of bahn.de's
"Verbindung" results — chips top-left, time range + duration, a segmented
timeline bar visualising the shape of the day (Anreise / Tour / Transfer /
Rückreise / Auto), origin/destination underneath, and a price rail on the
right.  A toggle below each row reveals the full chain (tour cards,
connection expanders, summary table).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd
import streamlit as st

from models import ChainLink, DayPlan, OptimizationResult
from .map import render_route_map


# --------------------------------------------------------------------------- #
# Number / time formatting
# --------------------------------------------------------------------------- #

def _fmt_eur(amount: float, sign: str = "") -> str:
    return f"{sign}{amount:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")


_BLANK_LINE_RE = re.compile(r"\n[ \t]*\n+")


def _html(html: str) -> None:
    """Emit raw HTML through st.markdown, stripping blank/whitespace-only
    lines that would otherwise terminate CommonMark's HTML block and cause the
    rest of the markup to render as plain text. The bug surfaces when an
    f-string substitution (e.g. an optional chip) is empty, leaving the line
    blank after interpolation."""
    cleaned = _BLANK_LINE_RE.sub("\n", html)
    st.markdown(cleaned, unsafe_allow_html=True)


def _fmt_duration(minutes: int) -> str:
    if minutes < 0:
        minutes = 0
    h, m = divmod(minutes, 60)
    if h == 0:
        return f"{m} min"
    if m == 0:
        return f"{h} h"
    return f"{h} h {m:02d} min"


# --------------------------------------------------------------------------- #
# Chain timeline derivation — handles car_outbound / car_inbound which
# don't carry a datetime themselves but offset the prior/next link.
# --------------------------------------------------------------------------- #

@dataclass
class _Segment:
    label: str          # short label rendered inside the segment when wide enough
    minutes: int
    kind: str           # 'tour' | 'transit' | 'car'
    title: str          # browser native tooltip


def _chain_start_dt(plan: DayPlan) -> Optional[datetime]:
    """First moment of the day. For car_outbound this is `next link departure
    minus drive_min`."""
    if not plan.chain:
        return None
    first = plan.chain[0]
    if first.connection and first.connection.departure_time:
        return first.connection.departure_time
    if first.tour:
        return first.tour.departure_dt
    drive_min = first.car_leg.minutes if first.car_leg else 0
    for link in plan.chain[1:]:
        if link.connection and link.connection.departure_time:
            return link.connection.departure_time - timedelta(minutes=drive_min)
        if link.tour:
            return link.tour.departure_dt - timedelta(minutes=drive_min)
    return None


def _chain_end_dt(plan: DayPlan) -> Optional[datetime]:
    """Last moment of the day. For car_inbound this is `prior link arrival
    plus drive_min`."""
    if not plan.chain:
        return None
    last = plan.chain[-1]
    if last.connection and last.connection.arrival_time:
        return last.connection.arrival_time
    if last.tour:
        return last.tour.arrival_dt
    drive_min = last.car_leg.minutes if last.car_leg else 0
    for link in reversed(plan.chain[:-1]):
        if link.connection and link.connection.arrival_time:
            return link.connection.arrival_time + timedelta(minutes=drive_min)
        if link.tour:
            return link.tour.arrival_dt + timedelta(minutes=drive_min)
    return None


def _start_station(plan: DayPlan) -> str:
    if not plan.chain:
        return "—"
    first = plan.chain[0]
    if first.connection and first.connection.legs:
        return first.connection.legs[0].departure_station
    if first.tour:
        return first.tour.departure_station
    if first.car_leg:
        return first.car_leg.from_station
    return "—"


def _end_station(plan: DayPlan) -> str:
    if not plan.chain:
        return "—"
    last = plan.chain[-1]
    if last.connection and last.connection.legs:
        return last.connection.legs[-1].arrival_station
    if last.tour:
        return last.tour.arrival_station
    if last.car_leg:
        return last.car_leg.from_station  # car_inbound returns to the start station
    return "—"


def _chain_segments(plan: DayPlan) -> list[_Segment]:
    """Walk the chain producing one Segment per link, keyed on time-share."""
    segs: list[_Segment] = []
    for link in plan.chain:
        if link.type == "tour" and link.tour:
            t = link.tour
            mins = max(1, int((t.arrival_dt - t.departure_dt).total_seconds() / 60))
            price = f"{t.euros:.2f}".replace(".", ",")
            segs.append(_Segment(
                label=str(t.tour_nr),
                minutes=mins,
                kind="tour",
                title=(
                    f"Tour № {t.tour_nr}  ·  "
                    f"{t.departure_time:%H:%M}–{t.arrival_time:%H:%M}  ·  "
                    f"{t.departure_station} → {t.arrival_station}  ·  "
                    f"{price} €"
                ),
            ))
        elif link.type in ("outbound", "transfer", "inbound") and link.connection:
            c = link.connection
            if not c.departure_time or not c.arrival_time:
                continue
            mins = max(1, int((c.arrival_time - c.departure_time).total_seconds() / 60))
            kind_word = {
                "outbound": "Anreise",
                "transfer": "Transfer",
                "inbound": "Rückreise",
            }[link.type]
            segs.append(_Segment(
                label="",
                minutes=mins,
                kind="transit",
                title=(
                    f"{kind_word}  ·  "
                    f"{c.departure_time:%H:%M}–{c.arrival_time:%H:%M}  ·  "
                    f"{c.duration_str} · {c.transfers} Umstieg(e)"
                ),
            ))
        elif link.type in ("car_outbound", "car_inbound") and link.car_leg:
            cl = link.car_leg
            cost = f"{cl.cost:.2f}".replace(".", ",")
            segs.append(_Segment(
                label="🚗",
                minutes=max(1, cl.minutes),
                kind="car",
                title=(
                    f"{link.label}  ·  "
                    f"{cl.minutes} min · {cl.km:.0f} km · {cost} €  ·  "
                    f"{cl.from_station} → {cl.to_station}"
                ),
            ))
    return segs


# --------------------------------------------------------------------------- #
# Detail blocks (preserved from previous design — used inside Details disclosure)
# --------------------------------------------------------------------------- #

def _render_tour_block(link: ChainLink) -> None:
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
            f"<div class='leg-line'>"
            f"<code>{leg.departure_time:%H:%M}</code> {leg.departure_station} "
            f"→ <code>{leg.arrival_time:%H:%M}</code> {leg.arrival_station} "
            f"<small>({leg.line}{sev_marker})</small>"
            f"</div>"
        )
    with st.expander(f"{title}  ·  {conn.duration_str} · {conn.transfers} Umstieg(e)"):
        st.markdown(f"{warning_html}{legs_html}", unsafe_allow_html=True)


def _render_car_leg_block(link: ChainLink) -> None:
    leg = link.car_leg
    if not leg:
        return
    cost_str = f"{leg.cost:.2f} €".replace(".", ",")
    st.markdown(
        f"""
        <div class="auto-leg">
          <span class="icon">🚗</span>
          <span class="label">{link.label}</span>
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
                "Euro": f"{t.euros:.2f} €".replace(".", ","),
            })
    if not rows:
        return
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


def _eyebrow(label: str, count_html: str = "") -> None:
    st.markdown(
        f"""
        <div class="result-eyebrow">
          <span class="result-eyebrow__label">{label}</span>
          {count_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


# --------------------------------------------------------------------------- #
# Bahn-style row
# --------------------------------------------------------------------------- #

def _bahn_time_range(
    start_dt: Optional[datetime], end_dt: Optional[datetime],
) -> tuple[str, int]:
    if start_dt and end_dt:
        return (
            f"{start_dt:%H:%M}&nbsp;–&nbsp;{end_dt:%H:%M}",
            int((end_dt - start_dt).total_seconds() / 60),
        )
    return "–", 0


def _bahn_segments_html(segments) -> str:
    """Build the colored bar segments. Each carries `--i` for the cascade
    animation, a flex-basis percentage, a kind class, and a tooltip. The
    `--narrow` modifier hides the CSS-pseudo icon (e.g. the train glyph in
    transit segments) when there isn't enough room to render it cleanly."""
    seg_total = sum(s.minutes for s in segments) or 1
    parts: list[str] = []
    for idx, s in enumerate(segments):
        width_pct = s.minutes / seg_total * 100
        narrow_cls = " bahn-seg--narrow" if width_pct < 4 else ""
        label_html = (
            f'<span class="bahn-seg__label">{s.label}</span>'
            if s.label and width_pct >= 6.5
            else ""
        )
        parts.append(
            f'<span class="bahn-seg bahn-seg--{s.kind}{narrow_cls}" '
            f'style="flex-basis:{width_pct:.3f}%; --i:{idx};" '
            f'title="{s.title}">{label_html}</span>'
        )
    return "".join(parts)


def _bahn_subhtml(plan: DayPlan) -> str:
    if plan.has_car_legs:
        return (
            f'<div class="bahn-row__rail-sub">'
            f'<span><span class="rs-k">Brutto</span><span class="rs-v">{_fmt_eur(plan.total_euros)}</span></span>'
            f'<span><span class="rs-k">Sprit</span><span class="rs-v">−{_fmt_eur(plan.total_costs)}</span></span>'
            f'</div>'
        )
    return (
        f'<div class="bahn-row__rail-sub">'
        f'<span><span class="rs-k">{plan.num_tours}</span><span class="rs-v">Touren</span></span>'
        f'</div>'
    )


def _bahn_overshoot_html(
    end_dt: Optional[datetime], latest_return_target: Optional[datetime],
) -> str:
    if latest_return_target is None or end_dt is None:
        return ""
    if end_dt <= latest_return_target + timedelta(minutes=1):
        return ""
    delta_min = int((end_dt - latest_return_target).total_seconds() / 60)
    h, m = divmod(delta_min, 60)
    delta_str = f"{h}h{m:02d}" if h > 0 else f"{m} Min"
    return (
        f'<span class="bahn-row__notice">'
        f'⚠ Rückkehr {delta_str} nach Wunschzeit '
        f'({latest_return_target:%H:%M})</span>'
    )


def _bahn_sev_chip(plan: DayPlan) -> str:
    has_sev = any(
        link.connection and link.connection.has_replacement_service
        for link in plan.chain
    )
    return (
        '<span class="bahn-row__sev" title="Schienenersatzverkehr im Plan">🚌 SEV</span>'
        if has_sev else ""
    )


def _render_bahn_row(
    plan: DayPlan,
    kind: str,                         # "winner" | "alternative"
    latest_return_target: Optional[datetime],
) -> None:
    start_dt = _chain_start_dt(plan)
    end_dt = _chain_end_dt(plan)
    time_range, total_min = _bahn_time_range(start_dt, end_dt)
    duration_str = _fmt_duration(total_min)

    seg_html = _bahn_segments_html(_chain_segments(plan))

    mode_word = "Auto" if plan.has_car_legs else "Transit"
    mode_class = "bahn-mode--car" if plan.has_car_legs else "bahn-mode--transit"

    rank_chip = (
        '<span class="bahn-rank bahn-rank--winner">Bester Plan</span>'
        if kind == "winner"
        else '<span class="bahn-rank bahn-rank--alt">Alternative</span>'
    )

    net_str = _fmt_eur(plan.net_euros)
    sub_html = _bahn_subhtml(plan)
    overshoot_html = _bahn_overshoot_html(end_dt, latest_return_target)
    tours_chip = f'<span class="bahn-row__tours">{plan.num_tours} Touren</span>'
    sev_chip = _bahn_sev_chip(plan)

    _html(f"""
        <article class="bahn-row bahn-row--{kind}">
          <div class="bahn-row__main">
            <div class="bahn-row__chips">
              {rank_chip}
              <span class="bahn-mode {mode_class}">
                <span class="bahn-mode__dot"></span>{mode_word}
              </span>
              {sev_chip}
              <span class="bahn-row__chips-spacer"></span>
              {overshoot_html}
            </div>
            <div class="bahn-row__head">
              <span class="bahn-row__time">{time_range}</span>
              <span class="bahn-row__sep">|</span>
              <span class="bahn-row__duration">{duration_str}</span>
              <span class="bahn-row__sep">·</span>
              {tours_chip}
            </div>
            <div class="bahn-row__bar" role="img" aria-label="Tagesverlauf">
              {seg_html}
            </div>
            <div class="bahn-row__route">
              <span class="bahn-row__route-from">{_start_station(plan)}</span>
              <span class="bahn-row__route-rail"></span>
              <span class="bahn-row__route-to">{_end_station(plan)}</span>
            </div>
          </div>
          <aside class="bahn-row__rail">
            <span class="bahn-row__rail-label">Netto</span>
            <span class="bahn-row__rail-amount">{net_str}</span>
            {sub_html}
          </aside>
        </article>
        """)


# --------------------------------------------------------------------------- #
# Details disclosure (warnings + map + chain blocks + summary)
# --------------------------------------------------------------------------- #

def _render_details(
    plan: DayPlan,
    kind: str,
    fuel_consumption: float,
    fuel_price: float,
    fuel_refund_per_km: float,
) -> None:
    for warning in plan.warnings:
        st.warning(warning)

    if plan.has_car_legs:
        total_km = sum(link.car_leg.km for link in plan.chain if link.car_leg is not None)
        cons_str = f"{fuel_consumption:.1f}".replace(".", ",")
        price_str = f"{fuel_price:.2f}".replace(".", ",")
        refund_ct_str = f"{fuel_refund_per_km * 100:.0f}".replace(".", ",")
        refund_suffix = (
            f" − {refund_ct_str} ct/km Pauschale" if fuel_refund_per_km > 0 else ""
        )
        st.caption(
            f"Sprit: {total_km:.0f} km · {cons_str} l/100km · {price_str} €/l"
            f"{refund_suffix} = {_fmt_eur(plan.total_costs)}"
        )

    if st.toggle(
        "Karte anzeigen",
        value=False,
        key=f"show_route_map_{kind}",
        help="Zeigt Anreise (grau), Touren (rot) und Rückreise (grau) auf einer Karte.",
    ):
        render_route_map(plan)

    chain_count = sum(1 for link in plan.chain if link.type == "tour")
    _eyebrow("Tagesplan", f'<span class="result-eyebrow__count">{chain_count} Touren</span>')
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

    _eyebrow("Zusammenfassung")
    _render_summary_table(plan)


def _render_plan_section(
    plan: DayPlan,
    kind: str,
    fuel_consumption: float,
    fuel_price: float,
    fuel_refund_per_km: float,
    latest_return_target: Optional[datetime],
) -> None:
    """One bahn-row + a Bahn.de-style 'Details ▾' link that reveals the chain."""
    _render_bahn_row(plan, kind, latest_return_target)

    # Details disclosure — rendered as a thin footer strip *inside* the card
    # via CSS (article opens at bottom, the marker + button containers below
    # carry matching side borders and the closing bottom-radius). Collapsed
    # by default for both winner and alternative.
    expanded_key = f"bahn_details_expanded_{kind}"
    expanded = st.session_state.get(expanded_key, False)

    # Marker carries `data-kind` so the alternative row can pick up its
    # gray accent rule on the footer strip via :has() selectors.
    st.markdown(
        f'<div class="bahn-details-toggle" data-kind="{kind}" '
        f'data-open="{"1" if expanded else "0"}"></div>',
        unsafe_allow_html=True,
    )
    chevron = "▴" if expanded else "▾"
    clicked = st.button(
        f"Details {chevron}",
        key=f"bahn_details_btn_{kind}",
        use_container_width=True,
    )
    if clicked:
        st.session_state[expanded_key] = not expanded
        st.rerun()

    if expanded:
        st.markdown('<div class="bahn-details-anchor"></div>', unsafe_allow_html=True)
        _render_details(plan, kind, fuel_consumption, fuel_price, fuel_refund_per_km)


# --------------------------------------------------------------------------- #
# Effort-ranked alternatives — compact list rendered under winner/alternative
# --------------------------------------------------------------------------- #

def _fmt_overhead(td: timedelta) -> str:
    """4h13 / 45 min — shorter than _fmt_duration for the compact row."""
    total_min = max(0, int(td.total_seconds() / 60))
    h, m = divmod(total_min, 60)
    if h == 0:
        return f"{m} min"
    return f"{h}h{m:02d}"


def _short_route_label(plan: DayPlan) -> str:
    """First tour number + 'Station → Station' across the whole chain."""
    if not plan.tours:
        return "—"
    first = plan.tours[0]
    last = plan.tours[-1]
    nrs = " · ".join(str(t.tour_nr) for t in plan.tours)
    return f"{nrs} — {first.departure_station} → {last.arrival_station}"


def _render_efficiency_row(
    plan: DayPlan,
    idx: int,
    fuel_consumption: float,
    fuel_price: float,
    fuel_refund_per_km: float,
    latest_return_target: Optional[datetime],
) -> None:
    """One compact row + Details disclosure reusing _render_details."""
    overhead_str = _fmt_overhead(plan.overhead_duration)
    eur_per_h_str = (
        f"{plan.euros_per_hour:.1f}".replace(".", ",") + " €/h"
        if plan.overhead_duration.total_seconds() > 0 else "—"
    )
    net_str = _fmt_eur(plan.net_euros)
    label = _short_route_label(plan)

    _html(f"""
        <article class="eff-row">
          <div class="eff-row__lead">
            <span class="eff-row__overhead">⏱ {overhead_str}</span>
            <span class="eff-row__label">{label}</span>
          </div>
          <div class="eff-row__meta">
            <span class="eff-row__net">{net_str}</span>
            <span class="eff-row__sep">·</span>
            <span class="eff-row__rate">{eur_per_h_str}</span>
          </div>
        </article>
        """)

    kind = f"eff_{idx}"
    expanded_key = f"bahn_details_expanded_{kind}"
    expanded = st.session_state.get(expanded_key, False)
    chevron = "▴" if expanded else "▾"
    if st.button(
        f"Details {chevron}",
        key=f"bahn_details_btn_{kind}",
        use_container_width=True,
    ):
        st.session_state[expanded_key] = not expanded
        st.rerun()
    if expanded:
        _render_details(plan, kind, fuel_consumption, fuel_price, fuel_refund_per_km)


def _render_efficiency_options(
    options: list[DayPlan],
    fuel_consumption: float,
    fuel_price: float,
    fuel_refund_per_km: float,
    latest_return_target: Optional[datetime],
) -> None:
    if not options:
        return
    _eyebrow(
        "Weitere Optionen — nach Aufwand sortiert",
        f'<span class="result-eyebrow__count">{len(options)} Optionen</span>',
    )
    for idx, plan in enumerate(options):
        _render_efficiency_row(
            plan, idx,
            fuel_consumption, fuel_price, fuel_refund_per_km,
            latest_return_target,
        )


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #

def render_result(result: OptimizationResult) -> None:
    """Render winner row + (optional) alternative row + effort-ranked options."""
    fuel_consumption = float(st.session_state.get("fuel_consumption", 7.0))
    fuel_price = float(st.session_state.get("fuel_price", 2.00))
    fuel_refund_per_km = float(st.session_state.get("fuel_refund_per_km", 0.20))
    latest_return_target = result.latest_return_target

    _render_plan_section(
        result.winner, "winner",
        fuel_consumption, fuel_price, fuel_refund_per_km, latest_return_target,
    )

    if result.has_alternative:
        _render_plan_section(
            result.alternative, "alternative",
            fuel_consumption, fuel_price, fuel_refund_per_km, latest_return_target,
        )

    _render_efficiency_options(
        result.efficiency_options,
        fuel_consumption, fuel_price, fuel_refund_per_km,
        latest_return_target,
    )
