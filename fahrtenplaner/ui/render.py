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

import streamlit as st

from models import DayPlan, OptimizationResult
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
    include_legs: bool = False,
) -> None:
    start_dt = _chain_start_dt(plan)
    end_dt = _chain_end_dt(plan)
    time_range, total_min = _bahn_time_range(start_dt, end_dt)
    duration_str = _fmt_duration(total_min)

    seg_html = _bahn_segments_html(_chain_segments(plan))

    # CSS classes share a single 'alternative' bucket regardless of rank
    # index — the per-kind suffix on `kind` is only used to keep session-state
    # keys unique across multiple ranked plans.
    is_winner = kind == "winner"
    css_kind = "winner" if is_winner else "alternative"
    rank_chip = (
        '<span class="bahn-rank bahn-rank--winner">Bester Plan</span>'
        if is_winner
        else '<span class="bahn-rank bahn-rank--alt">Alternative</span>'
    )

    net_str = _fmt_eur(plan.net_euros)
    sub_html = _bahn_subhtml(plan)
    overshoot_html = _bahn_overshoot_html(end_dt, latest_return_target)
    tours_chip = f'<span class="bahn-row__tours">{plan.num_tours} Touren</span>'
    sev_chip = _bahn_sev_chip(plan)

    legs_html = _build_legs_html(plan) if include_legs else ""

    _html(f"""
        <article class="bahn-row bahn-row--{css_kind}">
          <div class="bahn-row__main">
            <div class="bahn-row__chips">
              {rank_chip}
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
        {legs_html}
        """)


def _build_legs_html(plan: DayPlan) -> str:
    """Compact itinerary attached visually below the bahn-row card.

    Uses inline styles instead of CSS classes — Streamlit's markdown
    pipeline strips/wraps elements unpredictably, which breaks class-based
    sibling rules; inline styles are unaffected by that.
    """
    segs = _segments_from_chain(plan)
    if not segs:
        return ""
    # Color tokens chosen to match the existing bahn-row visuals.
    badge_styles = {
        "rail": "background:#1F2937;color:#fff;",
        "tour": "background:#E30613;color:#fff;",
        "car":  "background:#C8993A;color:#1A1A1A;",
        "walk": "background:#F4F4F2;color:#2C2C2A;border:1px solid #E3E3E0;",
    }
    row_bg = {
        "tour": "#FFF7F7", "car": "#FFFDF6", "walk": "#FAFAF8", "rail": "#FFFFFF",
    }
    rows: list[str] = []
    for i, s in enumerate(segs):
        dur = _fmt_minutes(
            int((s["arr_time"] - s["dep_time"]).total_seconds() / 60)
        )
        meta = (
            f'<span style="color:#666;font-size:0.82rem;font-variant-numeric:tabular-nums;'
            f'text-align:right;white-space:nowrap;">{s["meta"]}</span>'
        ) if s["meta"] else '<span></span>'
        bg = row_bg.get(s["kind"], "#FFF")
        border_top = "0" if i == 0 else "1px solid #EEEEEC"
        badge_style = badge_styles.get(s["kind"], "background:#1F2937;color:#fff;")
        route_weight = "600" if s["kind"] == "tour" else "500"
        rows.append(
            f'<div style="display:grid;'
            f'grid-template-columns:7.2rem 3.4rem auto 1fr auto;'
            f'align-items:baseline;column-gap:0.9rem;'
            f'padding:0.55rem 1rem;background:{bg};'
            f'border-top:{border_top};font-size:0.88rem;">'
            f'<span style="font-family:ui-monospace,Menlo,monospace;'
            f'font-variant-numeric:tabular-nums;font-weight:600;color:#1A1A1A;white-space:nowrap;">'
            f'{s["dep_time"]:%H:%M} → {s["arr_time"]:%H:%M}</span>'
            f'<span style="color:#666;font-size:0.78rem;font-variant-numeric:tabular-nums;">{dur}</span>'
            f'<span style="display:inline-block;min-width:6.5rem;text-align:center;'
            f'padding:0.18rem 0.6rem;border-radius:4px;font-size:0.72rem;'
            f'font-weight:600;letter-spacing:0.04em;white-space:nowrap;'
            f'{badge_style}align-self:center;">{s["badge"]}</span>'
            f'<span style="color:#1A1A1A;font-weight:{route_weight};overflow:hidden;'
            f'text-overflow:ellipsis;white-space:nowrap;">'
            f'{s["dep_station"]} → {s["arr_station"]}</span>'
            f'{meta}'
            f'</div>'
        )
    container_style = (
        "margin-top:-1px;border:1px solid #E3E3E0;border-top:0;"
        "border-radius:0 0 12px 12px;background:#FFFFFF;overflow:hidden;"
    )
    return f'<div style="{container_style}">{"".join(rows)}</div>'


# --------------------------------------------------------------------------- #
# Unified plan table — one row per leg (car, train, walk, tour)
# --------------------------------------------------------------------------- #

def _link_anchor_dt(link) -> Optional[datetime]:
    """Time anchor for a non-car link — the moment it 'starts' or 'ends'
    depending on caller intent. Returns None for car_outbound/car_inbound
    so they can't anchor each other."""
    if link.connection and link.connection.departure_time:
        return link.connection.departure_time
    if link.tour:
        return link.tour.departure_dt
    return None


def _link_end_dt(link) -> Optional[datetime]:
    if link.connection and link.connection.arrival_time:
        return link.connection.arrival_time
    if link.tour:
        return link.tour.arrival_dt
    return None


def _car_leg_times(plan: DayPlan, idx: int) -> tuple[Optional[datetime], Optional[datetime]]:
    """Compute (departure, arrival) for a car_outbound/car_inbound link.
    Anchored against the neighbouring non-car link's time."""
    link = plan.chain[idx]
    if not link.car_leg:
        return None, None
    drive = timedelta(minutes=link.car_leg.minutes)
    if link.type == "car_outbound":
        anchor = next(
            (dt for nxt in plan.chain[idx + 1:] if (dt := _link_anchor_dt(nxt))),
            None,
        )
        return (anchor - drive, anchor) if anchor else (None, None)
    # car_inbound
    anchor = next(
        (dt for prev in reversed(plan.chain[:idx]) if (dt := _link_end_dt(prev))),
        None,
    )
    return (anchor, anchor + drive) if anchor else (None, None)


def _fmt_hhmm(dt: Optional[datetime]) -> str:
    return f"{dt:%H:%M}" if dt is not None else "—"


def _fmt_minutes(minutes: int) -> str:
    if minutes < 60:
        return f"{minutes} min"
    h, m = divmod(minutes, 60)
    return f"{h}h{m:02d}"


def _segment_for_car(plan: DayPlan, idx: int) -> Optional[dict]:
    dep, arr = _car_leg_times(plan, idx)
    if dep is None or arr is None:
        return None
    cl = plan.chain[idx].car_leg
    meta_parts = [f"{cl.km:.0f} km"]
    if cl.cost != 0:
        meta_parts.append(f"{_fmt_eur(cl.cost)} Sprit")
    return {
        "kind": "car",
        "badge": "🚗  AUTO",
        "dep_time": dep, "dep_station": cl.from_station,
        "arr_time": arr, "arr_station": cl.to_station,
        "meta": " · ".join(meta_parts),
    }


def _segment_for_tour(link) -> dict:
    t = link.tour
    return {
        "kind": "tour",
        "badge": f"🎫  {t.tour_nr}",
        "dep_time": t.departure_dt, "dep_station": t.departure_station,
        "arr_time": t.arrival_dt, "arr_station": t.arrival_station,
        "meta": f"{_fmt_eur(t.euros, sign='+')} · {t.num_rides} Fahrt(en)",
    }


def _segment_for_leg(leg) -> dict:
    is_walk = leg.line == "🚶 Fußweg"
    sev = " · 🚌 SEV" if leg.is_replacement_service else ""
    badge = f"🚶  FUSSWEG{sev}" if is_walk else f"🚆  {leg.line}{sev}"
    return {
        "kind": "walk" if is_walk else "rail",
        "badge": badge,
        "dep_time": leg.departure_time,
        "dep_station": leg.departure_station or "—",
        "arr_time": leg.arrival_time,
        "arr_station": leg.arrival_station or "—",
        "meta": "",
    }


def _segments_from_chain(plan: DayPlan) -> list[dict]:
    """Flatten the chain to segments: one per car_leg, one per tour, one per
    transit Leg inside a Connection."""
    segs: list[dict] = []
    for idx, link in enumerate(plan.chain):
        if link.car_leg:
            seg = _segment_for_car(plan, idx)
            if seg is not None:
                segs.append(seg)
        elif link.tour:
            segs.append(_segment_for_tour(link))
        elif link.connection and link.connection.legs:
            segs.extend(_segment_for_leg(leg) for leg in link.connection.legs)
    return segs


# --------------------------------------------------------------------------- #
# Details disclosure — secondary info (warnings, sprit caption, map toggle).
# The leg-by-leg itinerary itself is embedded directly in the bahn-row card
# (see _build_legs_html above), so it's always visible without a click.
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


def _render_plan_section(
    plan: DayPlan,
    kind: str,
    fuel_consumption: float,
    fuel_price: float,
    fuel_refund_per_km: float,
    latest_return_target: Optional[datetime],
) -> None:
    """One bahn-row + a Bahn.de-style 'Details ▾' link that reveals the
    leg-by-leg itinerary (embedded in the card), warnings, and route map."""
    # Read the expansion state BEFORE rendering the bahn-row so the legs
    # can be embedded directly inside the same card when expanded.
    expanded_key = f"bahn_details_expanded_{kind}"
    expanded = st.session_state.get(expanded_key, False)

    _render_bahn_row(plan, kind, latest_return_target, include_legs=expanded)

    # Marker carries `data-kind` so the alternative row can pick up its
    # gray accent rule on the footer strip via :has() selectors. Normalise
    # the rank-suffix away so all non-winners share one class.
    css_kind = "winner" if kind == "winner" else "alternative"
    st.markdown(
        f'<div class="bahn-details-toggle" data-kind="{css_kind}" '
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
# Public entry point
# --------------------------------------------------------------------------- #

def render_result(result: OptimizationResult) -> None:
    """Render the top-N day plans as a uniform stack of bahn-row cards.
    Plan 0 gets the 'BESTER PLAN' label; the rest get 'ALTERNATIVE'.
    Plans are ranked by balanced score (net minus overhead-hours penalty)."""
    fuel_consumption = float(st.session_state.get("fuel_consumption", 7.0))
    fuel_price = float(st.session_state.get("fuel_price", 2.00))
    fuel_refund_per_km = float(st.session_state.get("fuel_refund_per_km", 0.20))
    latest_return_target = result.latest_return_target

    if not result.top_plans:
        return

    # Eyebrow with the default ranking-rule explanation as tooltip.
    st.markdown(
        '<div class="result-eyebrow">'
        '<span class="result-eyebrow__label">Top-Routen</span>'
        '<span class="result-eyebrow__hint" title="'
        'Empfehlung sortiert nach Netto-Vergütung. Routen mit mehr als 6h '
        'An- und Rückreisezeit werden pro zusätzlicher Stunde um 3 € '
        'abgewertet, damit lange Tage nicht über kompakten Touren landen.'
        '">ⓘ Wie wird sortiert?</span>'
        '</div>',
        unsafe_allow_html=True,
    )

    sort_state = _render_sort_controls()
    display_plans = _sort_top_plans(result.top_plans, sort_state)

    # The "Bester Plan" label always points to the score-winner
    # (top_plans[0]), no matter where it appears after a custom sort.
    score_winner = result.top_plans[0]
    for idx, plan in enumerate(display_plans):
        is_winner = plan is score_winner
        kind = "winner" if is_winner else f"alternative_{idx}"
        _render_plan_section(
            plan, kind,
            fuel_consumption, fuel_price, fuel_refund_per_km, latest_return_target,
        )


def _render_sort_controls() -> Optional[tuple[str, str]]:
    """Right-aligned compact sort controls: a criterion segmented-control
    paired with an asc/desc segmented-control. Returns (criterion, direction)
    or None when 'Empfehlung' is selected."""
    # Left half is empty (spacer) so the controls land on the right.
    _, control = st.columns([1, 1])
    with control:
        col_crit, col_dir = st.columns([4, 1])
        with col_crit:
            crit = st.segmented_control(
                "Sortieren",
                options=["Empfehlung", "Startzeit", "Dauer", "Verdienst"],
                default="Empfehlung",
                label_visibility="collapsed",
                key="result_sort_crit",
            )
        with col_dir:
            arrow = st.segmented_control(
                "Richtung",
                options=["↑", "↓"],
                default="↑",
                label_visibility="collapsed",
                key="result_sort_dir",
                disabled=(crit == "Empfehlung"),
            )

    if crit == "Empfehlung" or crit is None:
        return None
    direction = "desc" if arrow == "↓" else "asc"
    return (crit, direction)


def _sort_top_plans(
    plans: list[DayPlan],
    state: Optional[tuple[str, str]],
) -> list[DayPlan]:
    """Re-order the plans for display. None keeps the score-based order."""
    if state is None:
        return plans
    criterion, direction = state
    reverse = direction == "desc"

    if criterion == "Startzeit":
        return sorted(
            plans, key=lambda p: _chain_start_dt(p) or datetime.max,
            reverse=reverse,
        )
    if criterion == "Dauer":
        def _total_min(p: DayPlan) -> int:
            start, end = _chain_start_dt(p), _chain_end_dt(p)
            if start is None or end is None:
                return 24 * 60
            return int((end - start).total_seconds() / 60)
        return sorted(plans, key=_total_min, reverse=reverse)
    if criterion == "Verdienst":
        return sorted(plans, key=lambda p: p.net_euros, reverse=reverse)
    return plans
