"""Route visualization on a Carto basemap.

Tour segments render in DB Verkehrsrot; outbound/inbound/transfer in muted slate
to emphasize the *paid* portions of the day visually.
"""

from __future__ import annotations

import streamlit as st

from transit_client import lookup_station
from models import DayPlan


_MAP_COLORS = {
    "outbound":   [108, 122, 140, 210],   # muted slate (commuting)
    "tour":      [236,   0,  22, 235],   # Verkehrsrot — the paid work
    "transfer":  [170, 170, 165, 190],   # light gray (idle connection)
    "inbound": [108, 122, 140, 210],   # muted slate (return)
}

_MAP_TOOLTIP_STYLE = {
    "backgroundColor": "#FFFFFF",
    "color": "#0E0E0C",
    "fontFamily": "Onest, sans-serif",
    "fontSize": "12px",
    "padding": "6px 10px",
    "border": "1px solid #E3E3E0",
    "borderRadius": "8px",
    "boxShadow": "0 4px 12px rgba(0,0,0,0.08)",
}


def _collect_station_coords(plan: DayPlan) -> dict[str, tuple[float, float]]:
    """Resolve every station name in the chain to (lng, lat) via cached geocoding."""
    coords: dict[str, tuple[float, float]] = {}

    def add(name: str) -> None:
        if name in coords:
            return
        info = lookup_station(name)
        if info and info.get("location"):
            loc = info["location"]
            coords[name] = (float(loc["lng"]), float(loc["lat"]))

    for link in plan.chain:
        if link.tour:
            add(link.tour.departure_station)
            add(link.tour.arrival_station)
        if link.connection:
            for leg in link.connection.legs:
                add(leg.departure_station)
                add(leg.arrival_station)
    return coords


def _build_route_segments(
    plan: DayPlan, coords: dict[str, tuple[float, float]]
) -> list[dict]:
    """Build pydeck LineLayer segments with type-coded colors."""
    segments: list[dict] = []

    def push(a_name: str, b_name: str, ctype: str, label: str) -> None:
        a = coords.get(a_name)
        b = coords.get(b_name)
        if not a or not b or a == b:
            return
        segments.append({
            "from": [a[0], a[1]],
            "to":   [b[0], b[1]],
            "color": _MAP_COLORS.get(ctype, [120, 120, 120, 200]),
            "label": label,
        })

    for link in plan.chain:
        if link.type == "tour" and link.tour:
            push(
                link.tour.departure_station,
                link.tour.arrival_station,
                "tour",
                f"Tour {link.tour.tour_nr} · {link.tour.euros:.2f} €",
            )
        elif link.connection:
            for leg in link.connection.legs:
                push(
                    leg.departure_station,
                    leg.arrival_station,
                    link.type,
                    f"{link.type.capitalize()} · {leg.line}",
                )
    return segments


def _zoom_from_span(span: float) -> int:
    """Fallback zoom level for a given lat/lng span (degrees)."""
    if span >= 4:
        return 5
    if span >= 1.5:
        return 6
    if span >= 0.5:
        return 7
    return 9


def _compute_route_view_state(coords: dict[str, tuple[float, float]], pdk):
    """Mercator-aware bbox fit with a generous safety margin. Falls back to a heuristic."""
    point_list = [[lng, lat] for (lng, lat) in coords.values()]
    try:
        from pydeck.data_utils import compute_view
        view = compute_view(point_list, view_proportion=0.85)
        return pdk.ViewState(
            latitude=view.latitude,
            longitude=view.longitude,
            zoom=max(float(view.zoom) - 1.0, 4),
            pitch=0,
        )
    except Exception:
        lngs = [c[0] for c in coords.values()]
        lats = [c[1] for c in coords.values()]
        span = max(max(lats) - min(lats), max(lngs) - min(lngs)) or 0.1
        return pdk.ViewState(
            latitude=(min(lats) + max(lats)) / 2,
            longitude=(min(lngs) + max(lngs)) / 2,
            zoom=_zoom_from_span(span),
            pitch=0,
        )


def _render_map_legend() -> None:
    def bar(color: str) -> str:
        return (
            f'<svg width="22" height="4" style="vertical-align:middle;margin-right:6px;" '
            f'aria-hidden="true"><rect width="22" height="4" rx="2" fill="{color}"/></svg>'
        )

    st.markdown(
        f"""
        <div class="map-legend">
          <span class="leg">{bar('#EC0016')}Tour (bezahlt)</span>
          <span class="leg">{bar('#6C7A8C')}Anreise / Rückreise</span>
          <span class="leg">{bar('#AAAAA5')}Transfer</span>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_route_map(plan: DayPlan) -> None:
    """Render the daily route on a map. Tour segments in DB red, outbound/inbound muted."""
    if not plan.chain:
        return

    coords = _collect_station_coords(plan)
    if len(coords) < 2:
        st.caption("Karte: nicht genug Stationen mit Koordinaten gefunden.")
        return

    segments = _build_route_segments(plan, coords)
    if not segments:
        return

    try:
        import pydeck as pdk
    except ImportError:
        st.caption("Karte nicht verfügbar (pydeck fehlt).")
        return

    line_layer = pdk.Layer(
        "LineLayer", data=segments,
        get_source_position="from", get_target_position="to",
        get_color="color", get_width=5, pickable=True,
    )
    station_data = [{"name": n, "label": "", "position": [c[0], c[1]]} for n, c in coords.items()]
    station_layer = pdk.Layer(
        "ScatterplotLayer", data=station_data,
        get_position="position", get_color=[14, 14, 12, 230],
        get_radius=400, radius_min_pixels=4, radius_max_pixels=7,
        line_width_min_pixels=1, get_line_color=[255, 255, 255, 240],
        stroked=True, pickable=True,
    )

    deck = pdk.Deck(
        layers=[line_layer, station_layer],
        initial_view_state=_compute_route_view_state(coords, pdk),
        map_provider="carto", map_style="light",
        tooltip={"html": "<b>{name}{label}</b>", "style": _MAP_TOOLTIP_STYLE},
    )

    _render_map_legend()
    st.pydeck_chart(deck, use_container_width=True, height=320)
