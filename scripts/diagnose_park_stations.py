"""Diagnostic script: what does nearby_park_stations return for Prenzlau?

Run with:
    uv run python scripts/diagnose_park_stations.py

Reads GOOGLE_MAPS_API_KEY from .streamlit/secrets.toml (or env var).
Prints what Places API returns, what survives the S-Bahn filter, and what
the driving-radius filter keeps. Then attempts the hybrid Anreise lookup
for tour 721174 (Stralsund Hbf 06:12) from each surviving park-station.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

# Make fahrtenplaner importable as in the tests
sys.path.insert(0, str(Path(__file__).parent.parent / "fahrtenplaner"))

# Read API key from secrets.toml if present (mirrors transit_client._get_api_key)
def _load_api_key_from_secrets() -> None:
    import os
    secrets_path = Path(__file__).parent.parent / ".streamlit" / "secrets.toml"
    if not secrets_path.exists():
        return
    try:
        import tomllib  # Python 3.11+
        with open(secrets_path, "rb") as f:
            data = tomllib.load(f)
        key = data.get("GOOGLE_MAPS_API_KEY")
        if key and not os.environ.get("GOOGLE_MAPS_API_KEY"):
            os.environ["GOOGLE_MAPS_API_KEY"] = key
    except Exception as exc:
        print(f"WARN: could not read .streamlit/secrets.toml: {exc}")


_load_api_key_from_secrets()

from transit_client import (  # noqa: E402
    _is_pure_sbahn_name,
    check_reachability_with_ids,
    driving_info,
    lookup_station,
    nearby_park_stations,
)


HOME = "Prenzlau"
MAX_DRIVE = 60
EARLIEST = datetime(2026, 6, 1, 3, 0)
TOUR_START_DT = datetime(2026, 6, 1, 6, 12)
TOUR_START_STATION = "Stralsund Hbf"


def main() -> None:
    print("=" * 60)
    print(f"Diagnose nearby_park_stations({HOME!r}, max_drive={MAX_DRIVE})")
    print("=" * 60)

    home = lookup_station(HOME)
    if home is None:
        print(f"FAIL: lookup_station({HOME!r}) returned None")
        return
    print(f"Home geocoded: {home['id']} @ {home['location']}")

    # Call the real function
    try:
        stations = nearby_park_stations(HOME, MAX_DRIVE, top_k=15)
    except Exception as exc:
        print(f"FAIL: nearby_park_stations raised: {exc!r}")
        return

    print(f"\nFound {len(stations)} candidate park-stations:")
    for s in stations:
        info = lookup_station(s)
        if info:
            drive = driving_info(home["id"], info["id"])
            drive_str = f"{drive[0]} min, {drive[1]:.1f} km" if drive else "?"
        else:
            drive_str = "geocode failed"
        is_sbahn = _is_pure_sbahn_name(s)
        marker = " [S-BAHN-FILTERED]" if is_sbahn else ""
        print(f"  - {s:40s}  {drive_str}{marker}")

    # Test hybrid Anreise: each survivor → Stralsund Hbf by 06:07
    print(f"\nHybrid Anreise check: each park → {TOUR_START_STATION} by {TOUR_START_DT - timedelta(minutes=5):%H:%M}")
    tour_start = lookup_station(TOUR_START_STATION)
    if tour_start is None:
        print(f"FAIL: lookup_station({TOUR_START_STATION!r}) returned None")
        return
    print(f"Tour-start geocoded: {tour_start['id']}\n")

    for s in stations:
        info = lookup_station(s)
        if info is None:
            print(f"  - {s:40s}  geocode FAIL")
            continue
        drive = driving_info(home["id"], info["id"])
        if drive is None:
            print(f"  - {s:40s}  driving FAIL")
            continue
        drive_min, _ = drive
        car_arrival = EARLIEST + timedelta(minutes=drive_min)
        must_arrive = TOUR_START_DT - timedelta(minutes=5)
        conn = check_reachability_with_ids(
            info["id"], tour_start["id"], car_arrival, must_arrive,
        )
        if conn is None:
            print(f"  - {s:40s}  car@{car_arrival:%H:%M} → no train to {TOUR_START_STATION} by {must_arrive:%H:%M}")
        else:
            print(f"  - {s:40s}  car@{car_arrival:%H:%M} → train {conn.departure_time:%H:%M}→{conn.arrival_time:%H:%M} ({conn.duration_str}) ✓")


if __name__ == "__main__":
    main()
