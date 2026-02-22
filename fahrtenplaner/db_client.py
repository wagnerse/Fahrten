"""DB transport.rest API Client mit Caching und Rate-Limiting."""

from __future__ import annotations

import time as time_mod
from collections import deque
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from typing import Optional

import httpx
import streamlit as st

from models import Connection, Leg


BASE_URL = "https://v6.db.transport.rest"


# ---------------------------------------------------------------------------
# Rate Limiter (max 80 req/min)
# ---------------------------------------------------------------------------

class RateLimiter:
    """Einfacher Token-Bucket Rate Limiter."""

    def __init__(self, max_requests: int = 80, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window = window_seconds
        self._timestamps: deque[float] = deque()

    def wait(self):
        """Wartet falls nötig, bevor der nächste Request gesendet wird."""
        now = time_mod.time()
        # Alte Timestamps entfernen
        while self._timestamps and self._timestamps[0] < now - self.window:
            self._timestamps.popleft()
        if len(self._timestamps) >= self.max_requests:
            sleep_time = self._timestamps[0] + self.window - now + 0.1
            if sleep_time > 0:
                time_mod.sleep(sleep_time)
        self._timestamps.append(time_mod.time())


_rate_limiter = RateLimiter()
_http_client = httpx.Client(timeout=15.0)


# ---------------------------------------------------------------------------
# Station Lookup (gecacht 24h – NUR Erfolge werden gecacht)
# ---------------------------------------------------------------------------

@st.cache_data(ttl=86400, show_spinner=False)
def lookup_station(name: str) -> Optional[dict]:
    """
    Sucht eine Station nach Name. Gibt {id, name, location} zurück.

    Bei API-Fehlern wird eine Exception geworfen (→ wird NICHT gecacht).
    Nur echte "nicht gefunden"-Ergebnisse (API gibt 200 + leere Liste)
    werden als None gecacht.
    """
    _rate_limiter.wait()
    resp = _http_client.get(
        f"{BASE_URL}/locations",
        params={
            "query": name,
            "results": 1,
            "stops": "true",
            "addresses": "false",
            "poi": "false",
        },
    )
    resp.raise_for_status()  # HTTP-Fehler → Exception → nicht gecacht
    data = resp.json()
    if data and isinstance(data, list) and len(data) > 0:
        station = data[0]
        return {
            "id": station.get("id"),
            "name": station.get("name"),
            "location": station.get("location"),
        }
    return None  # Echt nicht gefunden → wird gecacht


# ---------------------------------------------------------------------------
# Connection Search (gecacht 1h)
# ---------------------------------------------------------------------------

@st.cache_data(ttl=3600, show_spinner=False)
def find_connection(
    from_id: str,
    to_id: str,
    departure: str,  # ISO format string für Caching
) -> Optional[Connection]:
    """Sucht eine Zugverbindung von A nach B ab Zeitpunkt."""
    _rate_limiter.wait()
    try:
        resp = _http_client.get(
            f"{BASE_URL}/journeys",
            params={
                "from": from_id,
                "to": to_id,
                "departure": departure,
                "results": 6,
                "transfers": 3,
                "national": "true",
                "nationalExpress": "true",
                "regional": "true",
                "regionalExpress": "true",
                "suburban": "true",
                "bus": "true",
                "tram": "true",
            },
        )
        resp.raise_for_status()
        data = resp.json()

        journeys = data.get("journeys", [])
        if not journeys:
            return None

        # Alle passenden Journeys parsen, beste (früheste Ankunft) zurückgeben
        best = None
        for journey in journeys:
            conn = _parse_journey(journey)
            if conn and conn.legs:
                if best is None or conn.arrival_time < best.arrival_time:
                    best = conn
        return best

    except Exception:
        pass
    return None


def _parse_journey(journey: dict) -> Optional[Connection]:
    """Parst ein Journey-Objekt in eine Connection."""
    legs_data = journey.get("legs", [])
    legs: list[Leg] = []

    for leg_data in legs_data:
        try:
            # Walking-Legs überspringen (haben kein 'line')
            if leg_data.get("walking"):
                continue

            dep_str = leg_data.get("departure")
            arr_str = leg_data.get("arrival")
            if not dep_str or not arr_str:
                continue

            dep_time = datetime.fromisoformat(dep_str.replace("Z", "+00:00"))
            arr_time = datetime.fromisoformat(arr_str.replace("Z", "+00:00"))

            # Naiv machen (UTC-Offset ignorieren, da alles in DE)
            dep_time = dep_time.replace(tzinfo=None)
            arr_time = arr_time.replace(tzinfo=None)

            line_data = leg_data.get("line", {})
            line_name = line_data.get("name", "?")

            # Schienenersatzverkehr erkennen
            product = line_data.get("product", "")
            product_name = line_data.get("productName", "")
            remarks = leg_data.get("remarks", [])
            is_replacement = (
                "bus" in product.lower() and "express" not in product.lower()
                or "SEV" in line_name.upper()
                or "ersatz" in product_name.lower()
                or any("ersatz" in str(r.get("text", "")).lower() for r in remarks)
            )

            origin = leg_data.get("origin", {})
            destination = leg_data.get("destination", {})

            legs.append(Leg(
                departure_station=origin.get("name", "?"),
                departure_time=dep_time,
                arrival_station=destination.get("name", "?"),
                arrival_time=arr_time,
                line=line_name,
                is_replacement_service=is_replacement,
            ))
        except Exception:
            continue

    if not legs:
        return None
    return Connection(legs=legs)


# ---------------------------------------------------------------------------
# Erreichbarkeits-Check (Hauptfunktion für Optimizer)
# ---------------------------------------------------------------------------

def check_reachability(
    from_station: str,
    to_station: str,
    earliest_departure: datetime,
    must_arrive_by: datetime,
    progress_callback=None,
) -> Optional[Connection]:
    """
    Prüft ob man von from_station nach to_station kommt.
    Gibt Connection zurück wenn rechtzeitig erreichbar, sonst None.
    """
    # Gleiche Station → kein Transfer nötig
    if _stations_match(from_station, to_station):
        return Connection(legs=[])

    try:
        from_info = lookup_station(from_station)
        to_info = lookup_station(to_station)
    except Exception:
        return None

    if not from_info or not to_info:
        return None

    conn = find_connection(
        from_info["id"],
        to_info["id"],
        earliest_departure.isoformat(),
    )

    if not conn or not conn.arrival_time:
        return None

    # Prüfe ob rechtzeitig angekommen
    if conn.arrival_time <= must_arrive_by:
        return conn

    return None


def _stations_match(a: str, b: str) -> bool:
    """Prüft ob zwei Stationsnamen die gleiche Station meinen."""
    def normalize(s: str) -> str:
        s = s.lower().strip()
        # Klammerzusätze entfernen
        s = s.split("(")[0].strip()
        # Häufige Suffixe
        for suffix in [" hbf", " hauptbahnhof", " bf"]:
            s = s.removesuffix(suffix)
        return s

    norm_a = normalize(a)
    norm_b = normalize(b)
    if norm_a == norm_b:
        return True
    # Enthaltensein prüfen (z.B. "Rostock" in "Rostock Hbf")
    if norm_a in norm_b or norm_b in norm_a:
        return True
    # Fuzzy: >90% Ähnlichkeit
    return SequenceMatcher(None, norm_a, norm_b).ratio() > 0.90


def stations_match(a: str, b: str) -> bool:
    """Öffentliche Version: Prüft ob zwei Stationsnamen die gleiche Station meinen."""
    return _stations_match(a, b)


def batch_lookup_stations(names: list[str]) -> dict[str, Optional[dict]]:
    """
    Löst eine Liste von Stationsnamen in Station-IDs auf.
    Retry bei API-Fehlern (bis zu 3 Versuche pro Station).
    """
    result: dict[str, Optional[dict]] = {}
    for name in names:
        if name not in result:
            for attempt in range(3):
                try:
                    result[name] = lookup_station(name)
                    break
                except Exception:
                    if attempt < 2:
                        time_mod.sleep(1)
                    else:
                        result[name] = None
    return result


def check_reachability_with_ids(
    from_id: str,
    to_id: str,
    earliest_departure: datetime,
    must_arrive_by: datetime,
) -> Optional[Connection]:
    """
    Prüft Erreichbarkeit mit bereits aufgelösten Station-IDs.
    Spart redundante lookup_station() Aufrufe.
    Bei Fehlschlag: Retry mit +15 Min falls genug Zeitfenster.
    """
    conn = find_connection(from_id, to_id, earliest_departure.isoformat())
    if conn and conn.arrival_time and conn.arrival_time <= must_arrive_by:
        return conn

    # Retry: +15 Min später, falls genug Zeitfenster
    retry_dep = earliest_departure + timedelta(minutes=15)
    if retry_dep < must_arrive_by - timedelta(minutes=30):
        conn = find_connection(from_id, to_id, retry_dep.isoformat())
        if conn and conn.arrival_time and conn.arrival_time <= must_arrive_by:
            return conn

    return None
