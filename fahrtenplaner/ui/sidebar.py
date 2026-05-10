"""All sidebar widgets, grouped into named "kit" panels:

    Datenquelle  →  MyRES credentials + Bundesländer + Datum + Touren laden
    Route        →  Abfahrts- / Ankunftsbahnhof
    Auto-Anfahrt →  max. Fahrzeit + Verbrauch + Spritpreis + Kosten-Vorschau

Plus the small auxiliary blocks (demo data, tour stats, update panel,
debug, footer) which keep their flat layout.

Returns a SidebarContext object the main pane uses to render the optimization.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

import streamlit as st

from models import Tour
from myres_client import MyRESClient, load_tours_from_excel
from updater import current_version
from .errors import report_error
from .update_panel import render_update_panel


_GERMAN_STATES = [
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
    max_car_minutes: int


# --------------------------------------------------------------------------- #
# Small presentation helpers
# --------------------------------------------------------------------------- #

def _kit_eyebrow(label: str, status_html: str = "") -> None:
    """Section eyebrow above each card: 'DATENQUELLE  ●  verbunden'."""
    st.sidebar.markdown(
        f"""
        <div class="kit-eyebrow">
          <span class="kit-eyebrow__label">{label}</span>
          {status_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


def _myres_status_html() -> str:
    """Inline pill: connection status of MyRES based on session state."""
    tours = st.session_state.tours
    client = st.session_state.myres_client
    if tours and client is not None:
        return (
            f'<span class="kit-status kit-status--ok">'
            f'<span class="kit-status__dot"></span>{len(tours)} Touren</span>'
        )
    if tours:
        return (
            f'<span class="kit-status kit-status--neutral">'
            f'<span class="kit-status__dot"></span>{len(tours)} Touren · offline</span>'
        )
    return (
        '<span class="kit-status kit-status--idle">'
        '<span class="kit-status__dot"></span>nicht verbunden</span>'
    )


def _car_status_html(max_car_minutes: int, same_station: bool) -> str:
    if not same_station:
        return (
            '<span class="kit-status kit-status--off">'
            '<span class="kit-status__dot"></span>nur bei Ankunft = Abfahrt</span>'
        )
    if max_car_minutes <= 0:
        return (
            '<span class="kit-status kit-status--idle">'
            '<span class="kit-status__dot"></span>aus</span>'
        )
    return (
        f'<span class="kit-status kit-status--ok">'
        f'<span class="kit-status__dot"></span>aktiv · ≤ {max_car_minutes} Min</span>'
    )


# --------------------------------------------------------------------------- #
# Panel: Datenquelle (MyRES)
# --------------------------------------------------------------------------- #

def _render_data_source_panel() -> tuple[str, str, list[str], date]:
    """The MyRES card: credentials, Bundesländer, Datum, primary load button."""
    # Consume any deferred Datum update queued before this run (e.g. by the
    # demo-data loader, which can't write to `datum_input` directly because
    # the widget is instantiated *before* the button click is processed).
    if "pending_datum" in st.session_state:
        st.session_state.datum_input = st.session_state.pop("pending_datum")

    _kit_eyebrow("Datenquelle", _myres_status_html())

    with st.sidebar.container(border=True):
        with st.expander("MyRES Zugangsdaten", expanded=False):
            username = st.text_input(
                "Username",
                value=st.session_state.myres_username,
                key="myres_user_input",
            )
            password = st.text_input(
                "Passwort",
                value=st.session_state.myres_password,
                type="password",
                key="myres_pw_input",
            )
            st.session_state.myres_username = username
            st.session_state.myres_password = password
            st.caption(
                "Wird nur lokal in der Sitzung gehalten — landet nicht in Logs.",
            )

        states = st.multiselect(
            "Bundesländer",
            _GERMAN_STATES,
            default=["Brandenburg", "Mecklenburg-Vorpommern"],
            help="Welche Länder soll MyRES nach freien Touren absuchen?",
        )

        selected_date = st.date_input(
            "Datum",
            key="datum_input",
            help="Tag der Erhebungsfahrt.",
        )

        load_clicked = st.button(
            "Touren laden",
            type="primary",
            use_container_width=True,
            help="MyRES kontaktieren und freie Touren für den Tag holen.",
        )

    if load_clicked:
        _handle_load_tours(
            st.session_state.myres_username,
            st.session_state.myres_password,
            states,
            selected_date,
        )

    return (
        st.session_state.myres_username,
        st.session_state.myres_password,
        states,
        selected_date,
    )


# --------------------------------------------------------------------------- #
# Panel: Route (stations)
# --------------------------------------------------------------------------- #

def _render_route_panel() -> tuple[str, str, bool]:
    _kit_eyebrow("Route")

    with st.sidebar.container(border=True):
        home = st.text_input(
            "Abfahrtsbahnhof",
            value="Prenzlau",
            help="Von wo aus startet ihr morgens?",
        )
        same = st.checkbox(
            "Ankunft = Abfahrt",
            value=True,
            help="Endet ihr abends wieder am Startbahnhof?",
        )
        if same:
            return home, home, True

        dest = st.text_input(
            "Ankunftsbahnhof",
            value="Stralsund",
            help="Wo wollt ihr abends ankommen?",
        )
        return home, dest, False


# --------------------------------------------------------------------------- #
# Panel: Auto-Anfahrt (driving radius + fuel)
# --------------------------------------------------------------------------- #

def _render_auto_panel(same_station: bool) -> int:
    """All driving-mode config in one place: radius slider + fuel + preview.

    Returns the selected `max_car_minutes` (0 = Auto-Modus aus).
    """
    # Force off when the constraint isn't satisfied
    if not same_station and st.session_state.max_car_minutes > 0:
        st.session_state.max_car_minutes = 0

    _kit_eyebrow(
        "Auto-Anfahrt",
        _car_status_html(int(st.session_state.max_car_minutes), same_station),
    )

    with st.sidebar.container(border=True):
        max_minutes = st.slider(
            "Max. Auto-Fahrzeit (Min.)",
            min_value=0,
            max_value=120,
            step=5,
            value=int(st.session_state.max_car_minutes),
            disabled=not same_station,
            help=(
                "Wie weit dürft ihr morgens mit dem Auto fahren, um den Startbahnhof "
                "zu erreichen?  0 = ausschließlich ÖPNV."
                if same_station
                else "Auto-Modus erfordert Ankunft = Abfahrt."
            ),
        )
        st.session_state.max_car_minutes = int(max_minutes)

        if max_minutes <= 0:
            st.markdown(
                """
                <div class="kit-empty">
                  Auto-Modus ist <strong>aus</strong>.
                  Schiebt den Regler nach rechts, um Park-and-Ride-Optionen zu prüfen.
                </div>
                """,
                unsafe_allow_html=True,
            )
            return 0

        cons = float(st.session_state.fuel_consumption)
        price = float(st.session_state.fuel_price)

        col_a, col_b = st.columns(2)
        with col_a:
            st.session_state.fuel_consumption = st.number_input(
                "Verbrauch (l/100 km)",
                min_value=0.0, max_value=30.0, step=0.1,
                value=cons,
                help="Wert aus dem Bordcomputer oder Fahrzeugschein.",
            )
        with col_b:
            st.session_state.fuel_price = st.number_input(
                "Preis (€/l)",
                min_value=0.0, max_value=5.0, step=0.01,
                value=price,
                help="Aktueller Preis an der Tankstelle.",
            )

        per_km_cents = (cons * price)  # ct/km == l/100 × €/l × 100 ÷ 100
        # Rough envelope: assume avg 70 km/h on rural Anfahrt, one-way × 2.
        approx_km_round_trip = (max_minutes / 60.0) * 70.0 * 2
        approx_cost_round_trip = approx_km_round_trip * per_km_cents / 100.0

        st.markdown(
            f"""
            <div class="kit-readout">
              <div class="kit-readout__row">
                <span class="kit-readout__label">Kosten je km</span>
                <span class="kit-readout__value">{per_km_cents:.1f}<span class="kit-readout__unit">ct</span></span>
              </div>
              <div class="kit-readout__row">
                <span class="kit-readout__label">≈ Fahrt H&nbsp;&amp;&nbsp;R</span>
                <span class="kit-readout__value">{approx_km_round_trip:.0f}<span class="kit-readout__unit">km · {approx_cost_round_trip:.2f} €</span></span>
              </div>
              <div class="kit-readout__hint">
                Annahme&nbsp;⌀ 70 km/h, Hin- und Rückweg.
                Tatsächliche Kosten variieren je Park-and-Ride-Bahnhof.
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    return int(max_minutes)


# --------------------------------------------------------------------------- #
# MyRES fetch with exponential backoff
# --------------------------------------------------------------------------- #
#
# The IVV-Berlin WAF intermittently 403s our impersonated TLS handshake; the
# user's empirical workaround was to mash the button 2–3 times. We do that
# automatically with a fresh client per attempt — a new curl_cffi Session can
# settle on a different cipher/extension order, which often gets through.

_MAX_LOAD_ATTEMPTS = 3
_BACKOFF_BASE_SECONDS = 1.0
_BACKOFF_JITTER_SECONDS = 0.6   # added uniformly at random per retry

# Rotated across attempts. Each profile carries a different JA3/JA4
# fingerprint, so when the WAF has cached a 403 verdict against the default
# 'chrome' profile, attempt 2 presents a different handshake to the WAF.
_IMPERSONATE_PROFILES = ("chrome", "chrome120", "chrome116")


def _is_credentials_error(error_msg: str) -> bool:
    """Permanent failure — retrying won't help."""
    return "falsche zugangsdaten" in (error_msg or "").lower()


def _attempt_load_once(
    username: str, password: str, states: list[str], selected_date: date,
    impersonate: str,
) -> tuple[list[Tour], MyRESClient, Optional[Exception]]:
    """One full login + fetch attempt with a fresh client using the given
    impersonation profile. Returns `(tours, client, exception_or_None)`.
    An empty `tours` plus no `client.last_error` and no exception means the
    request succeeded but legitimately returned zero rows."""
    client = MyRESClient(impersonate=impersonate)
    if not client.login(username, password):
        return [], client, None
    try:
        tours = client.fetch_free_tours(states, selected_date, selected_date)
        return tours, client, None
    except Exception as exc:
        return [], client, exc


@dataclass
class _LoadOutcome:
    tours: list[Tour]
    client: Optional[MyRESClient]
    succeeded: bool
    last_error_msg: str
    final_exc: Optional[Exception]


def _attempt_label(attempt: int, profile: str) -> str:
    if attempt == 1:
        return f"Verbinde mit MyRES ({profile})..."
    return (
        f"Versuch {attempt}/{_MAX_LOAD_ATTEMPTS} mit anderem "
        f"Profil ({profile})..."
    )


def _backoff_seconds(attempt: int) -> float:
    """Exponential backoff plus uniform jitter so synchronized retries
    don't all land in the same WAF cooldown window."""
    return (
        _BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
        + random.uniform(0, _BACKOFF_JITTER_SECONDS)
    )


def _run_with_retries(
    status, username: str, password: str, states: list[str], selected_date: date,
) -> _LoadOutcome:
    """Loop login+fetch up to `_MAX_LOAD_ATTEMPTS` times, rotating the TLS
    impersonation profile each round and applying exponential backoff between
    attempts."""
    outcome = _LoadOutcome(tours=[], client=None, succeeded=False,
                           last_error_msg="", final_exc=None)

    for attempt in range(1, _MAX_LOAD_ATTEMPTS + 1):
        profile = _IMPERSONATE_PROFILES[(attempt - 1) % len(_IMPERSONATE_PROFILES)]
        status.update(label=_attempt_label(attempt, profile))

        tours, client, exc = _attempt_load_once(
            username, password, states, selected_date, impersonate=profile,
        )
        outcome.tours = tours
        outcome.client = client

        if exc is None and not client.last_error:
            # Either real tours, or honest empty (no error set).
            outcome.succeeded = True
            return outcome

        outcome.last_error_msg = client.last_error or (str(exc) if exc else "")
        outcome.final_exc = exc

        # Don't waste backoff on permanently-bad credentials, and skip the
        # final sleep — fall through to error reporting.
        if _is_credentials_error(outcome.last_error_msg):
            return outcome
        if attempt == _MAX_LOAD_ATTEMPTS:
            return outcome

        wait_s = _backoff_seconds(attempt)
        status.update(
            label=(
                f"Fehler ({outcome.last_error_msg[:60]}…) — "
                f"warte {wait_s:.1f}s, dann erneuter Versuch."
            )
        )
        time.sleep(wait_s)

    return outcome


def _finalize_status(status, outcome: _LoadOutcome) -> None:
    if outcome.succeeded and outcome.tours:
        status.update(label=f"{len(outcome.tours)} Touren geladen!", state="complete")
    elif outcome.succeeded:
        status.update(label="Keine Touren gefunden", state="complete")
    elif _is_credentials_error(outcome.last_error_msg):
        status.update(label="Login fehlgeschlagen", state="error")
    else:
        status.update(
            label=f"Touren-Abruf fehlgeschlagen nach {_MAX_LOAD_ATTEMPTS} Versuchen",
            state="error",
        )


def _report_load_failure(
    outcome: _LoadOutcome, username: str, states: list[str], date_str: str,
) -> None:
    if _is_credentials_error(outcome.last_error_msg):
        title = "Anmeldung bei MyRES fehlgeschlagen"
    else:
        title = f"Touren-Abruf fehlgeschlagen nach {_MAX_LOAD_ATTEMPTS} Versuchen"
    details = (
        f"MyRES-Fehlermeldung: {outcome.last_error_msg or '—'}\n"
        f"Benutzername: {username}\n"
        f"Bundesländer: {', '.join(states)}\n"
        f"Datum: {date_str}"
    )
    report_error(title, details=details, exc=outcome.final_exc)


def _handle_load_tours(
    username: str, password: str, states: list[str], selected_date: date,
) -> None:
    """Run the MyRES login + fetch flow with exponential-backoff retries on
    transient (WAF/network) failures. Surface persistent errors via the error
    dialog."""
    if not username or not password:
        st.sidebar.error("Zugangsdaten fehlen — öffne 'MyRES Zugangsdaten' oben.")
        return

    with st.sidebar.status("Verbinde mit MyRES...") as status:
        outcome = _run_with_retries(
            status, username, password, states, selected_date,
        )
        _finalize_status(status, outcome)

    # Always remember the most recent client (helps debug after a failure).
    if outcome.client is not None:
        st.session_state.myres_client = outcome.client

    if outcome.succeeded:
        st.session_state.tours = outcome.tours
        if outcome.tours:
            _signal_tours_just_loaded(
                prev_plan_existed=st.session_state.get("last_plan") is not None,
            )
        else:
            st.sidebar.warning(
                "Keine Touren im gewählten Zeitraum. Bundesländer / Datum anpassen?"
            )
        return

    _report_load_failure(
        outcome, username, states, selected_date.strftime("%d.%m.%Y"),
    )


# --------------------------------------------------------------------------- #
# Auxiliary blocks (kept flat under the cards)
# --------------------------------------------------------------------------- #

def _render_demo_data_button() -> None:
    if not _DEMO_EXCEL.exists():
        return
    st.sidebar.divider()
    has_tours = bool(st.session_state.tours)
    label = "Demo-Daten neu laden" if has_tours else "Demo-Daten laden (BB+MV April)"
    if st.sidebar.button(label, use_container_width=True):
        tours = load_tours_from_excel(_DEMO_EXCEL)
        st.session_state.tours = tours
        # Queue the Datum picker change for the *next* run. Writing directly to
        # `datum_input` here would raise StreamlitAPIException — the widget was
        # already instantiated above this button in the current run.
        if tours:
            st.session_state.pending_datum = min(t.date for t in tours)
            _signal_tours_just_loaded(prev_plan_existed=st.session_state.get("last_plan") is not None)
        st.rerun()


def _signal_tours_just_loaded(*, prev_plan_existed: bool) -> None:
    """Reset the optimization slot and raise flags the main pane consumes
    on the next render: a green confirmation banner, an auto-expanded tour
    browser, and (if a plan was discarded) a small caption explaining why."""
    st.session_state.last_plan = None
    st.session_state.last_plan_log = []
    st.session_state.tours_just_loaded = True
    if prev_plan_existed:
        st.session_state.tours_reload_discarded = True


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
                from transit_client import lookup_station
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


# --------------------------------------------------------------------------- #
# Top-level
# --------------------------------------------------------------------------- #

def render_sidebar() -> SidebarContext:
    """Render the entire sidebar; return the user-selected configuration."""
    _username, _password, _states, selected_date = _render_data_source_panel()
    home_station, dest_station, same_station = _render_route_panel()
    max_car_minutes = _render_auto_panel(same_station)

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
        max_car_minutes=max_car_minutes,
    )
