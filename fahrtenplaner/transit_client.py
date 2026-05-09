"""Google Maps Directions API Client for transit connections."""

from __future__ import annotations

import os
import time as time_mod
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from typing import Optional

import logging

import googlemaps

from models import Connection, Leg

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Google Maps Client
# ---------------------------------------------------------------------------

_gmaps: Optional[googlemaps.Client] = None


def _get_api_key() -> str:
    """Read API key from st.secrets (preferred) or env var."""
    try:
        import streamlit as st
        return st.secrets["GOOGLE_MAPS_API_KEY"]
    except Exception:
        pass
    return os.environ.get("GOOGLE_MAPS_API_KEY", "")


def _get_client() -> googlemaps.Client:
    global _gmaps
    if _gmaps is None:
        key = _get_api_key()
        if not key:
            raise RuntimeError(
                "GOOGLE_MAPS_API_KEY not set. "
                "Add it to .streamlit/secrets.toml or export as env variable."
            )
        _gmaps = googlemaps.Client(key=key)
    return _gmaps


# ---------------------------------------------------------------------------
# Caches (simple dicts — no streamlit dependency needed)
# ---------------------------------------------------------------------------

_station_cache: dict[str, Optional[dict]] = {}
_connection_cache: dict[str, Optional[Connection]] = {}


# ---------------------------------------------------------------------------
# Station Lookup
# ---------------------------------------------------------------------------

def lookup_station(name: str) -> Optional[dict]:
    """Resolve a station name to {id (place_id), name, location}."""
    if name in _station_cache:
        return _station_cache[name]

    try:
        client = _get_client()
        # Try with "Bahnhof" first, fallback to just the name.
        # Use region="de" bias instead of appending "Deutschland" to avoid
        # mis-geocoding cross-border stations (Szczecin, Swinoujscie, etc.)
        for query in [f"{name} Bahnhof", f"{name}"]:
            results = client.geocode(query, language="de", region="de")
            if results:
                break
        if results:
            place = results[0]
            info = {
                "id": place["place_id"],
                "name": place.get("formatted_address", name),
                "location": place["geometry"]["location"],
            }
            _station_cache[name] = info
            return info
    except Exception as e:
        logger.error("lookup_station(%s) failed: %s", name, e)

    _station_cache[name] = None
    return None


def batch_lookup_stations(names: list[str]) -> dict[str, Optional[dict]]:
    """Resolve a list of station names to place IDs."""
    result: dict[str, Optional[dict]] = {}
    for name in names:
        if name not in result:
            result[name] = lookup_station(name)
    return result


# ---------------------------------------------------------------------------
# Connection Search
# ---------------------------------------------------------------------------

def find_connection(
    from_id: str,
    to_id: str,
    departure: str,  # ISO format string
) -> Optional[Connection]:
    """Find a transit connection from A to B starting at departure time."""
    cache_key = f"{from_id}|{to_id}|{departure}"
    if cache_key in _connection_cache:
        return _connection_cache[cache_key]

    try:
        client = _get_client()
        dep_dt = datetime.fromisoformat(departure)

        routes = client.directions(
            origin=f"place_id:{from_id}",
            destination=f"place_id:{to_id}",
            mode="transit",
            transit_mode=["rail", "train", "tram"],
            departure_time=dep_dt,
            alternatives=True,
            language="de",
        )

        if not routes:
            _connection_cache[cache_key] = None
            return None

        # Pick the route with earliest arrival
        best: Optional[Connection] = None
        for route in routes:
            conn = _parse_route(route)
            if conn and conn.legs:
                if best is None or conn.arrival_time < best.arrival_time:
                    best = conn

        _connection_cache[cache_key] = best
        return best

    except Exception as e:
        logger.error("find_connection(%s → %s) failed: %s", from_id, to_id, e)

    _connection_cache[cache_key] = None
    return None


def _is_replacement_service(line_name: str, vehicle_type: str) -> bool:
    """A bus line whose name contains SEV/ERSATZ is a Schienenersatzverkehr."""
    if vehicle_type != "BUS":
        return False
    upper = line_name.upper()
    return any(kw in upper for kw in ("SEV", "ERSATZ"))


def _parse_transit_step(step: dict) -> Optional[Leg]:
    """Parse a single Google Maps step into a Leg, or return None if it's not transit."""
    if step.get("travel_mode") != "TRANSIT":
        return None

    td = step.get("transit_details") or {}
    dep_ts = td.get("departure_time", {}).get("value")
    arr_ts = td.get("arrival_time", {}).get("value")
    if not dep_ts or not arr_ts:
        return None

    line_info = td.get("line", {})
    line_name = line_info.get("short_name") or line_info.get("name") or "?"
    vehicle_type = line_info.get("vehicle", {}).get("type", "")

    return Leg(
        departure_station=td.get("departure_stop", {}).get("name", "?"),
        departure_time=datetime.fromtimestamp(dep_ts),
        arrival_station=td.get("arrival_stop", {}).get("name", "?"),
        arrival_time=datetime.fromtimestamp(arr_ts),
        line=line_name,
        is_replacement_service=_is_replacement_service(line_name, vehicle_type),
    )


def _parse_route(route: dict) -> Optional[Connection]:
    """Parse a Google Maps route into a Connection with transit Legs."""
    parsed_legs: list[Leg] = []
    for leg in route.get("legs", []):
        for step in leg.get("steps", []):
            parsed = _parse_transit_step(step)
            if parsed is not None:
                parsed_legs.append(parsed)
    return Connection(legs=parsed_legs) if parsed_legs else None


# ---------------------------------------------------------------------------
# Reachability Check (main interface for optimizer)
# ---------------------------------------------------------------------------

def check_reachability_with_ids(
    from_id: str,
    to_id: str,
    earliest_departure: datetime,
    must_arrive_by: datetime,
) -> Optional[Connection]:
    """Check if a transit connection exists that arrives before the deadline."""
    conn = find_connection(from_id, to_id, earliest_departure.isoformat())
    if conn and conn.arrival_time and conn.arrival_time <= must_arrive_by:
        return conn

    # Retry with +30 min if enough time window remains
    retry_dep = earliest_departure + timedelta(minutes=30)
    if retry_dep < must_arrive_by - timedelta(minutes=30):
        conn = find_connection(from_id, to_id, retry_dep.isoformat())
        if conn and conn.arrival_time and conn.arrival_time <= must_arrive_by:
            return conn

    return None


# ---------------------------------------------------------------------------
# Station Name Matching (no API needed)
# ---------------------------------------------------------------------------

def _stations_match(a: str, b: str) -> bool:
    """Check if two station names refer to the same station."""
    def normalize(s: str) -> str:
        s = s.lower().strip()
        s = s.split("(")[0].strip()
        for suffix in [" hbf", " hauptbahnhof", " bf"]:
            s = s.removesuffix(suffix)
        return s

    norm_a = normalize(a)
    norm_b = normalize(b)
    if norm_a == norm_b:
        return True
    if norm_a in norm_b or norm_b in norm_a:
        return True
    return SequenceMatcher(None, norm_a, norm_b).ratio() > 0.90


def stations_match(a: str, b: str) -> bool:
    """Public version: check if two station names refer to the same station."""
    return _stations_match(a, b)
