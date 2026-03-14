"""Google Maps Directions API Client for transit connections."""

from __future__ import annotations

import os
import time as time_mod
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from typing import Optional

import googlemaps

from models import Connection, Leg


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
        # Try with "Bahnhof" first, fallback to just the name
        for query in [f"{name} Bahnhof, Deutschland", f"{name}, Deutschland"]:
            results = client.geocode(query, language="de")
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
    except Exception:
        pass

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

    except Exception:
        pass

    _connection_cache[cache_key] = None
    return None


def _parse_route(route: dict) -> Optional[Connection]:
    """Parse a Google Maps route into a Connection with transit Legs."""
    legs_data = route.get("legs", [])
    if not legs_data:
        return None

    parsed_legs: list[Leg] = []

    for leg in legs_data:
        for step in leg.get("steps", []):
            if step.get("travel_mode") != "TRANSIT":
                continue

            td = step.get("transit_details", {})
            if not td:
                continue

            dep_ts = td.get("departure_time", {}).get("value")
            arr_ts = td.get("arrival_time", {}).get("value")
            if not dep_ts or not arr_ts:
                continue

            dep_time = datetime.fromtimestamp(dep_ts)
            arr_time = datetime.fromtimestamp(arr_ts)

            line_info = td.get("line", {})
            line_name = line_info.get("short_name", line_info.get("name", "?"))

            dep_stop = td.get("departure_stop", {}).get("name", "?")
            arr_stop = td.get("arrival_stop", {}).get("name", "?")

            # Detect replacement bus services
            vehicle = line_info.get("vehicle", {})
            vehicle_type = vehicle.get("type", "")
            is_replacement = (
                vehicle_type == "BUS"
                and any(kw in line_name.upper() for kw in ["SEV", "ERSATZ"])
            )

            parsed_legs.append(Leg(
                departure_station=dep_stop,
                departure_time=dep_time,
                arrival_station=arr_stop,
                arrival_time=arr_time,
                line=line_name,
                is_replacement_service=is_replacement,
            ))

    if not parsed_legs:
        return None
    return Connection(legs=parsed_legs)


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
