"""Google Maps Directions API Client for transit connections."""

from __future__ import annotations

import os
import re
import time as time_mod
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from typing import Optional

import logging

import googlemaps

from models import Connection, Leg

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class TransitClientError(Exception):
    """Raised when a Google Maps API call fails in a way that should bubble
    up to the UI as a user-visible error rather than degrading silently.

    Today's pattern in this module (e.g. lookup_station, find_connection)
    is to log + return None on any exception. That works for routing-style
    calls where the optimizer treats None as "unreachable". For
    `nearby_park_stations`, however, returning [] silently would hide
    Places API quota exhaustion or auth failures from the user. This
    exception lets the UI render a specific dialog instead.
    """


# ---------------------------------------------------------------------------
# S-Bahn name filter
# ---------------------------------------------------------------------------

# Conservative pattern — must only match unambiguous S-Bahn-only naming.
# Cities starting with "S" followed by a letter (Stralsund, Senftenberg,
# Schwedt, Spandau, ...) are NOT matched.
_S_BAHN_NAME_PATTERN: re.Pattern[str] = re.compile(
    r"^(S\s|S\+)"           # "S Wedding", "S+U Friedrichstraße"
    r"|\bS-Bahn(hof)?\b"    # "S-Bahn Wedding", "S-Bahnhof Köpenick"
    r"|\s\(S\)$"            # "Berlin (S)" — some data sources
)


def _is_pure_sbahn_name(name: str) -> bool:
    """True iff the station name unambiguously looks like an S-Bahn-only stop."""
    return bool(_S_BAHN_NAME_PATTERN.search(name))


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
_driving_cache: dict[str, Optional[tuple[int, float]]] = {}

# Module-level cache for nearby_park_stations. Key: (home_station_name,
# max_drive_minutes). Lifetime: same as the other caches — survives Streamlit
# reruns within one process, resets on worker restart.
_park_stations_cache: dict[tuple[str, int], list[str]] = {}


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


def driving_info(from_id: str, to_id: str) -> Optional[tuple[int, float]]:
    """One-way driving time and distance between two place_ids.

    Returns (minutes, km) or None if no driving route is found. Cached
    indefinitely per (from_id, to_id) pair — drive times are stable enough.
    """
    cache_key = f"{from_id}|{to_id}"
    if cache_key in _driving_cache:
        return _driving_cache[cache_key]

    try:
        client = _get_client()
        routes = client.directions(
            origin=f"place_id:{from_id}",
            destination=f"place_id:{to_id}",
            mode="driving",
            language="de",
        )
        if not routes:
            _driving_cache[cache_key] = None
            return None

        leg = routes[0]["legs"][0]
        seconds = int(leg["duration"]["value"])
        meters = int(leg["distance"]["value"])
        result = (seconds // 60, meters / 1000.0)
        _driving_cache[cache_key] = result
        return result
    except Exception as e:
        logger.error("driving_info(%s → %s) failed: %s", from_id, to_id, e)
        _driving_cache[cache_key] = None
        return None


# ---------------------------------------------------------------------------
# Park-station discovery (Google Places Nearby Search)
# ---------------------------------------------------------------------------

def nearby_park_stations(
    home_station: str,
    max_drive_minutes: int,
    top_k: int = 15,
) -> list[str]:
    """Discover train stations within driving radius of home.

    Calls Google Maps Places Nearby Search once per session per
    (home_station, max_drive_minutes) pair (cached). Filters out S-Bahn-only
    stops by name pattern, drops stations outside the driving radius using
    driving_info, drops the home station itself, sorts by drive_min asc,
    truncates to top_k.

    Raises TransitClientError if home_station cannot be geocoded or if
    Places Nearby Search fails. Returns [] when the API succeeds but no
    surviving candidates exist.
    """
    cache_key = (home_station, max_drive_minutes)
    if cache_key in _park_stations_cache:
        return _park_stations_cache[cache_key]

    home = lookup_station(home_station)
    if home is None or not home.get("location") or not home.get("id"):
        raise TransitClientError(
            f"Heimatbahnhof '{home_station}' konnte nicht geocodiert werden."
        )
    home_lat = home["location"]["lat"]
    home_lng = home["location"]["lng"]
    home_id = home["id"]

    # Places Nearby Search caps radius at 50 km. With ~60 km/h secondary-road
    # speed the Luftlinie radius equals about max_drive_minutes; the cap
    # only matters for unusually large max_drive_minutes settings.
    radius_km = min(50, max_drive_minutes)
    radius_m = radius_km * 1000

    try:
        client = _get_client()
        response = client.places_nearby(
            location=(home_lat, home_lng),
            radius=radius_m,
            type="train_station",
            language="de",
        )
    except Exception as exc:
        raise TransitClientError(
            f"Google Places Nearby Search fehlgeschlagen: {exc}"
        ) from exc

    results = response.get("results", []) if isinstance(response, dict) else []

    # Filter, geocode, drive-time check
    candidates_with_time: list[tuple[int, str]] = []
    for entry in results:
        name = entry.get("name")
        if not name:
            continue
        if _is_pure_sbahn_name(name):
            continue
        # Drop home itself by name (cheap pre-check before geocoding)
        if name == home_station:
            continue
        # Geocode via the standard pipeline so the result is consistent with
        # how the optimizer treats stations elsewhere.
        info = lookup_station(name)
        if not info or not info.get("id"):
            continue
        # Skip home if name differed but geocoded to the same place_id
        if info["id"] == home_id:
            continue
        drive = driving_info(home_id, info["id"])
        if drive is None:
            continue
        drive_min, _drive_km = drive
        if drive_min > max_drive_minutes:
            continue
        candidates_with_time.append((drive_min, name))

    # Deduplicate exact-name repeats (Places API sometimes returns the same
    # station twice). Keep the first occurrence (closest by drive after sort).
    candidates_with_time.sort(key=lambda pair: pair[0])
    seen: set[str] = set()
    park_stations: list[str] = []
    for _drive, name in candidates_with_time:
        if name in seen:
            continue
        seen.add(name)
        park_stations.append(name)
        if len(park_stations) >= top_k:
            break

    _park_stations_cache[cache_key] = park_stations
    return park_stations


def _is_replacement_service(line_name: str, vehicle_type: str) -> bool:
    """A bus line whose name contains SEV/ERSATZ is a Schienenersatzverkehr."""
    if vehicle_type != "BUS":
        return False
    upper = line_name.upper()
    return any(kw in upper for kw in ("SEV", "ERSATZ"))


def _parse_transit_step(step: dict) -> Optional[Leg]:
    """Parse a single Google Maps TRANSIT step into a Leg."""
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


_WALKING_LINE_LABEL: str = "🚶 Fußweg"
# Skip walks shorter than this — they're typically platform changes within
# the same station and just clutter the chain rendering.
_MIN_WALK_SECONDS: int = 120


def _parse_route(route: dict) -> Optional[Connection]:
    """Parse a Google Maps route into a Connection of TRANSIT + WALKING legs.
    Walking is kept so a transfer like Pasewalk Ost → Pasewalk Hbf is visible
    in the chain (and counted in overhead time)."""
    parsed_legs: list[Leg] = []
    for outer_leg in route.get("legs", []):
        dep_ts = outer_leg.get("departure_time", {}).get("value")
        if dep_ts is None:
            # No time anchor → can't position walking legs; skip them.
            for step in outer_leg.get("steps", []):
                if step.get("travel_mode") == "TRANSIT":
                    parsed = _parse_transit_step(step)
                    if parsed is not None:
                        parsed_legs.append(parsed)
            continue

        # Running clock: TRANSIT steps carry absolute timestamps; WALKING
        # uses the clock advanced by the step's duration.
        current_dt = datetime.fromtimestamp(dep_ts)
        last_arrival_station: str = ""
        for step in outer_leg.get("steps", []):
            mode = step.get("travel_mode")
            if mode == "TRANSIT":
                parsed = _parse_transit_step(step)
                if parsed is None:
                    continue
                parsed_legs.append(parsed)
                current_dt = parsed.arrival_time
                last_arrival_station = parsed.arrival_station
            elif mode == "WALKING":
                duration_sec = step.get("duration", {}).get("value", 0)
                walk_end = current_dt + timedelta(seconds=duration_sec)
                # Always advance the clock, but only emit a Leg when the walk
                # is non-trivial — short walks are platform changes within a
                # station, not real transfers.
                if duration_sec >= _MIN_WALK_SECONDS:
                    parsed_legs.append(Leg(
                        departure_station=last_arrival_station,
                        departure_time=current_dt,
                        arrival_station="",  # filled below
                        arrival_time=walk_end,
                        line=_WALKING_LINE_LABEL,
                        is_replacement_service=False,
                    ))
                current_dt = walk_end

    # Fill arrival_station from the next non-walking leg. Walking legs with
    # no following transit are trailing platform-to-exit walks and get dropped
    # — the user is already at the destination station for our purposes.
    filled: list[Leg] = []
    for i, leg in enumerate(parsed_legs):
        if leg.line == _WALKING_LINE_LABEL and not leg.arrival_station:
            target = next(
                (n.departure_station for n in parsed_legs[i + 1:]
                 if n.line != _WALKING_LINE_LABEL),
                "",
            )
            if not target:
                continue  # trailing walk → drop
            leg.arrival_station = target
        filled.append(leg)

    return Connection(legs=filled) if filled else None


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
