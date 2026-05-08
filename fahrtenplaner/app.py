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
from db_client import lookup_station
from updater import (
    GITHUB_REPO,
    current_version,
    download_update,
    fetch_latest_release,
    is_newer,
)

# Pfad zur lokalen Demo-Excel (für Entwicklung, falls MyRES nicht erreichbar)
_DEMO_EXCEL = Path(__file__).parent.parent / "Freie_Touren_BB_MV_01-03_April_2026.xlsx"

# ---------------------------------------------------------------------------
# Page Config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Erhebungsfahrten-Planer",
    page_icon="🚂",
    layout="wide",
    initial_sidebar_state="expanded",
    menu_items={"Get help": None, "Report a bug": None, "About": None},
)

# ---------------------------------------------------------------------------
# Styles — Bahnamt edition
# ---------------------------------------------------------------------------

_CSS_PATH = Path(__file__).parent / "assets" / "style.css"


@st.cache_data
def _load_stylesheet(_mtime_ns: int) -> str:
    """mtime_ns participates in the cache key so saving style.css invalidates the cache."""
    return _CSS_PATH.read_text(encoding="utf-8") if _CSS_PATH.exists() else ""


_css = _load_stylesheet(_CSS_PATH.stat().st_mtime_ns if _CSS_PATH.exists() else 0)
if _css:
    st.markdown(f"<style>{_css}</style>", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Error Reporting — copyable modal so Dad can forward issues to Sebastian
# ---------------------------------------------------------------------------

@st.dialog("Fehler – bitte an Sebastian senden", width="large")
def _show_error_dialog(title: str, details: str, timestamp: str) -> None:
    st.markdown(f"**{title}**")
    st.write(
        "Bitte schicke Sebastian den folgenden Text. "
        "Mit dem Symbol oben rechts im Feld kannst du alles kopieren."
    )
    body = (
        f"Fahrtenplaner v{current_version()}\n"
        f"Zeitpunkt: {timestamp}\n\n"
        f"{title}\n"
        f"--------------------------------\n"
        f"{details or '(keine weiteren Details)'}\n"
    )
    st.code(body, language="text")
    if st.button("Schließen", use_container_width=True):
        st.rerun()


def _report_error(title: str, details: str = "", exc: BaseException | None = None) -> None:
    """Open a modal showing a copyable error report. Single funnel for all surface errors."""
    import traceback
    parts = []
    if details:
        parts.append(details)
    if exc is not None:
        parts.append(f"{type(exc).__name__}: {exc}")
        tb = traceback.format_exc()
        if tb and tb.strip() and tb.strip() != "NoneType: None":
            parts.append(tb.rstrip())
    full = "\n\n".join(p for p in parts if p)
    ts = datetime.now().isoformat(timespec="seconds")
    _show_error_dialog(title, full, ts)


# ---------------------------------------------------------------------------
# Session State
# ---------------------------------------------------------------------------

if "tours" not in st.session_state:
    st.session_state.tours = []
if "myres_client" not in st.session_state:
    st.session_state.myres_client = None
myres_defaults = st.secrets.get("myres", {})
if "myres_username" not in st.session_state:
    st.session_state.myres_username = str(myres_defaults.get("username", ""))
if "myres_password" not in st.session_state:
    st.session_state.myres_password = str(myres_defaults.get("password", ""))


# ---------------------------------------------------------------------------
# Sidebar – MyRES Login & Touren laden
# ---------------------------------------------------------------------------

with st.sidebar.expander("MyRES Zugangsdaten"):
    username = st.text_input("Username", value=st.session_state.myres_username, key="myres_user_input")
    password = st.text_input("Passwort", value=st.session_state.myres_password, type="password", key="myres_pw_input")
    st.session_state.myres_username = username
    st.session_state.myres_password = password

st.sidebar.markdown("### Filter")
states = st.sidebar.multiselect(
    "Bundesländer",
    ["Brandenburg", "Mecklenburg-Vorpommern", "Sachsen", "Sachsen-Anhalt",
     "Thüringen", "Berlin", "Niedersachsen", "Schleswig-Holstein"],
    default=["Brandenburg", "Mecklenburg-Vorpommern"],
)

selected_date = st.sidebar.date_input("Datum", value=date.today())

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
        st.sidebar.error("Zugangsdaten fehlen – klicke ⚙️ oben")
    else:
        _fetch_failed = False
        _fetch_exc: Exception | None = None
        with st.sidebar.status("Verbinde mit MyRES...") as status:
            client = MyRESClient()
            if not client.login(username, password):
                status.update(label="Login fehlgeschlagen", state="error")
                _fetch_failed = True
                _fetch_error_title = "Anmeldung bei MyRES fehlgeschlagen"
                _fetch_error_details = (
                    f"MyRES-Fehlermeldung: {client.last_error}\n"
                    f"Benutzername: {username}\n"
                    f"Bundesländer: {', '.join(states)}\n"
                    f"Datum: {selected_date.strftime('%d.%m.%Y')}"
                )
            else:
                status.update(label="Login OK – lade Touren...")
                try:
                    tours = client.fetch_free_tours(states, selected_date, selected_date)
                except Exception as e:
                    tours = []
                    _fetch_failed = True
                    _fetch_exc = e
                    _fetch_error_title = "Touren-Abruf fehlgeschlagen (Ausnahme)"
                    _fetch_error_details = (
                        f"Bundesländer: {', '.join(states)}\n"
                        f"Datum: {selected_date.strftime('%d.%m.%Y')}"
                    )

                st.session_state.tours = tours
                st.session_state.myres_client = client

                if tours:
                    status.update(label=f"{len(tours)} Touren geladen!", state="complete")
                elif client.last_error:
                    status.update(label="Touren-Abruf fehlgeschlagen", state="error")
                    _fetch_failed = True
                    _fetch_error_title = "Touren-Abruf fehlgeschlagen"
                    _fetch_error_details = (
                        f"MyRES-Fehlermeldung: {client.last_error}\n"
                        f"Bundesländer: {', '.join(states)}\n"
                        f"Datum: {selected_date.strftime('%d.%m.%Y')}"
                    )
                elif not _fetch_failed:
                    status.update(label="Keine Touren gefunden", state="complete")
                    st.sidebar.warning(
                        "Keine Touren im gewählten Zeitraum. "
                        "Bundesländer / Datum anpassen?"
                    )

        if _fetch_failed:
            _report_error(
                _fetch_error_title,
                details=_fetch_error_details,
                exc=_fetch_exc,
            )

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
    dates = sorted({t.date for t in tours})
    st.sidebar.metric("Tage", len(dates))
    total_euros = sum(t.euros for t in tours)
    st.sidebar.metric("Gesamtwert", f"{total_euros:.0f} €")


# ---------------------------------------------------------------------------
# Hauptbereich
# ---------------------------------------------------------------------------

st.markdown(
    f"""
    <div class="studio-hero">
        <div class="eyebrow">
            <span class="db-mark">DB</span>
            <span>Zugzählung · Erhebungsfahrten</span>
            <span class="ver">v{current_version()}</span>
        </div>
        <h1 class="title">Fahrten<span class="accent">planer</span></h1>
        <p class="subtitle">Optimale Tourenketten für die DB-Zugzählung. Anreise,
        Touren, Transfers und Rückreise als ein durchgehender Tagesplan.</p>
    </div>
    """,
    unsafe_allow_html=True,
)

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
    st.success("Optimale Route gefunden!")
    m1, m2, m3 = st.columns(3)
    m1.metric("Gesamtverdienst", f"{plan.total_euros:.2f} €")
    m2.metric("Anzahl Touren", plan.num_tours)
    m3.metric("Zeitraum", plan.time_range)

    # Warnungen
    for warning in plan.warnings:
        st.warning(warning)

    st.divider()

    # Karte (optional, standardmäßig aus — schont Platz auf der Seite)
    if st.toggle(
        "Tagesroute auf Karte anzeigen",
        value=False,
        key="show_route_map",
        help="Zeigt Anreise (grau), Touren (rot) und Rückreise (grau) auf einer Karte.",
    ):
        _render_route_map(plan)

    st.divider()

    # Tagesplan
    st.subheader("Tagesplan")

    for i, link in enumerate(plan.chain):
        if link.type == "anreise":
            _render_connection_block("🚉 Anreise", link)
        elif link.type == "tour":
            _render_tour_block(link)
        elif link.type == "transfer":
            _render_connection_block("🔄 Transfer", link)
        elif link.type == "rückreise":
            _render_connection_block("🏠 Rückreise", link)

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


_MAP_COLORS = {
    "anreise":   [108, 122, 140, 210],   # muted slate (commuting)
    "tour":      [236,   0,  22, 235],   # Verkehrsrot — the paid work
    "transfer":  [170, 170, 165, 190],   # light gray (idle connection)
    "rückreise": [108, 122, 140, 210],   # muted slate (return)
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


def _render_route_map(plan: DayPlan) -> None:
    """Render the daily route on a map. Tour segments in DB red, anreise/rückreise muted."""
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


def _render_tour_block(link: ChainLink):
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


def _render_connection_block(title: str, link: ChainLink):
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


# ---------------------------------------------------------------------------
# Hauptbereich – Single-Flow: Plan-Kontext → Optimierung → Tour-Browser
# ---------------------------------------------------------------------------

# Persisted optimization result so it survives reruns.
if "last_plan" not in st.session_state:
    st.session_state.last_plan = None
if "last_plan_log" not in st.session_state:
    st.session_state.last_plan_log = []

day_tours = [t for t in tours if t.date == selected_date]
_WEEKDAYS_DE = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
_day_label = f"{_WEEKDAYS_DE[selected_date.weekday()]} · {selected_date.strftime('%d.%m.%Y')}"
_route_value = (
    f'<span class="station">{home_station}</span>'
    f'<span class="arrow">→</span>'
    f'<span class="station">{home_station if same_station else dest_station}</span>'
)

# --- Plan-context strip --------------------------------------------------- #
st.markdown(
    f"""
    <div class="plan-strip">
      <div class="item">
        <span class="label">Datum</span>
        <span class="value">{_day_label}</span>
      </div>
      <div class="sep">·</div>
      <div class="item">
        <span class="label">Touren am Tag</span>
        <span class="value">{len(day_tours)}</span>
      </div>
      <div class="sep">·</div>
      <div class="item">
        <span class="label">Route</span>
        <span class="value">{_route_value}</span>
      </div>
    </div>
    """,
    unsafe_allow_html=True,
)


# --- Optimization panel (PRIMARY) ----------------------------------------- #
st.markdown(
    """
    <div class="section-head">
      <h2>Optimale Tourenkette berechnen</h2>
      <p class="lede">Anreise · Touren · Transfers · Rückreise – als ein durchgehender Tagesplan.</p>
    </div>
    """,
    unsafe_allow_html=True,
)

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


col1, col2, col3 = st.columns(3)
with col1:
    _dep_str = st.text_input(
        "Früheste Abfahrt",
        value="04:00",
        placeholder="HH:MM",
        help="Frühester Start zuhause — Format HH:MM, z. B. 04:00",
    )
    dep_time, _dep_ok = _parse_hhmm(_dep_str, time(4, 0))
    if not _dep_ok and _dep_str.strip():
        st.caption(":red[Bitte HH:MM eingeben — z. B. 04:00]")
with col2:
    _ret_str = st.text_input(
        "Späteste Rückkehr",
        value="23:59",
        placeholder="HH:MM",
        help="Spätestes Eintreffen am Zielbahnhof — Format HH:MM, z. B. 23:59",
    )
    ret_time, _ret_ok = _parse_hhmm(_ret_str, time(23, 59))
    if not _ret_ok and _ret_str.strip():
        st.caption(":red[Bitte HH:MM eingeben — z. B. 23:59]")
with col3:
    max_gap_minutes = st.number_input(
        "Max. Pause zwischen Touren (Min.)",
        min_value=10,
        max_value=1440,
        value=60,
        step=10,
        help="Maximale Zeit zwischen Ende einer Tour und Beginn der nächsten (inkl. Leerfahrt)",
    )

if st.button(
    "Optimale Route berechnen",
    type="primary",
    use_container_width=True,
    disabled=not day_tours or not home_station,
):
    earliest = datetime.combine(selected_date, dep_time)
    latest = datetime.combine(selected_date, ret_time)
    if latest <= earliest:
        latest += timedelta(days=1)

    progress_bar = st.progress(0, text="Starte Optimierung...")
    log_messages: list[str] = []

    def progress_cb(pct: float, msg: str):
        progress_bar.progress(min(pct, 1.0), text=msg)
        log_messages.append(msg)

    _opt_exc: Exception | None = None
    try:
        plan = optimize_day(
            tours=day_tours,
            home_station=home_station,
            dest_station=dest_station,
            earliest_departure=earliest,
            latest_return=latest,
            progress_callback=progress_cb,
            max_transfer_gap_hours=max_gap_minutes / 60,
        )
    except Exception as e:
        plan = DayPlan()
        _opt_exc = e

    progress_bar.empty()
    st.session_state.last_plan = plan
    st.session_state.last_plan_log = log_messages

    if _opt_exc is not None:
        _report_error(
            "Optimierung fehlgeschlagen",
            details=(
                f"Datum: {selected_date.strftime('%d.%m.%Y')}\n"
                f"Route: {home_station} → "
                f"{home_station if same_station else dest_station}\n"
                f"Touren am Tag: {len(day_tours)}\n"
                f"Fenster: {dep_time:%H:%M}–{ret_time:%H:%M}, "
                f"max. Pause: {max_gap_minutes} Min\n"
                f"Letzter Schritt: {(log_messages[-1] if log_messages else '—')}"
            ),
            exc=_opt_exc,
        )

# --- Result area ---------------------------------------------------------- #
_plan = st.session_state.last_plan
if _plan is None:
    if not day_tours:
        st.markdown(
            f"""
            <div class="empty-result">
              <strong>Keine Touren am {_day_label.split('·')[1].strip()}.</strong>
              <span class="hint">Wähle ein anderes Datum oder lade die Touren neu.</span>
            </div>
            """,
            unsafe_allow_html=True,
        )
    elif not home_station:
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
        st.markdown(
            """
            <div class="empty-result">
              Klicke <strong>Optimale Route berechnen</strong>,
              um die einträglichste Tourenkette für den ausgewählten Tag zu finden.
              <span class="hint">Die Berechnung prüft Anreise, Transfers und Rückreise mit Google Maps Transit.</span>
            </div>
            """,
            unsafe_allow_html=True,
        )
elif _plan.num_tours == 0:
    st.warning(
        "Keine gültige Tourenkette gefunden. Versuche:\n"
        "- Früheste Abfahrt vorverlegen\n"
        "- Späteste Rückkehr nach hinten schieben\n"
        "- Anderen Start-/Zielbahnhof wählen"
    )
    if st.session_state.last_plan_log:
        with st.expander("Optimierungsdetails"):
            for msg in st.session_state.last_plan_log:
                st.text(msg)
else:
    _render_result(_plan)
    if st.session_state.last_plan_log:
        with st.expander("Optimierungsdetails"):
            for msg in st.session_state.last_plan_log:
                st.text(msg)


# --- Tour browser (SECONDARY, collapsed) ---------------------------------- #
with st.expander(f"Alle verfügbaren Touren  ·  {len(tours)} insgesamt", expanded=True):
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


# ---------------------------------------------------------------------------
# Footer
# ---------------------------------------------------------------------------

st.sidebar.divider()


@st.cache_data(ttl=600, show_spinner=False)
def _check_for_update(_repo: str):
    return fetch_latest_release()


with st.sidebar.expander(f"Über / Update — v{current_version()}"):
    if GITHUB_REPO == "OWNER/REPO":
        st.caption("Auto-Update nicht konfiguriert (Dev-Modus).")
    else:
        release = _check_for_update(GITHUB_REPO)
        if release is None:
            st.caption("Update-Server nicht erreichbar.")
        elif is_newer(release.tag, current_version()):
            st.markdown(f"**Neue Version verfügbar: {release.tag}**")
            if release.body:
                with st.expander("Was ist neu?"):
                    st.markdown(release.body)

            if st.session_state.get("update_staged"):
                st.success("Update geladen. Bitte App neu starten.")
            else:
                if st.button("Aktualisieren", type="primary", use_container_width=True):
                    progress = st.progress(0.0, text="Lade Update...")
                    _update_exc: Exception | None = None
                    try:
                        download_update(release, progress=lambda p: progress.progress(p))
                    except Exception as e:
                        _update_exc = e
                    progress.empty()
                    if _update_exc is None:
                        st.session_state.update_staged = True
                        st.success("Update geladen. Bitte App neu starten.")
                        st.rerun()
                    else:
                        _report_error(
                            "Update-Download fehlgeschlagen",
                            details=(
                                f"Aktuelle Version: {current_version()}\n"
                                f"Ziel-Version: {release.tag}\n"
                                f"Asset-URL: {release.asset_url}"
                            ),
                            exc=_update_exc,
                        )
        else:
            st.caption("Auf neuestem Stand ✓")

st.sidebar.divider()
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
                capture_output=True, text=True, timeout=15
            )
            st.code(f"stdout: {result.stdout}\nstderr: {result.stderr}")
        except Exception as e:
            st.exception(e)

    import platform
    st.text(f"Python: {platform.python_version()}")
    st.text(f"Platform: {platform.platform()}")

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
