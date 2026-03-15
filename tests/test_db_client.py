"""Tests for db_client Google Maps integration — mocked, no real API calls."""

import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "fahrtenplaner"))

from models import Connection, Leg


# ---------------------------------------------------------------------------
# Sample Google Maps API responses
# ---------------------------------------------------------------------------

GMAPS_DIRECTIONS_PRENZLAU_WARNEMUENDE = {
    "status": "OK",
    "routes": [{
        "legs": [{
            "departure_time": {"value": 1743487260},  # 2026-04-01 07:01 CEST
            "arrival_time": {"value": 1743504480},     # 2026-04-01 11:48 CEST
            "steps": [
                {
                    "travel_mode": "TRANSIT",
                    "transit_details": {
                        "line": {"short_name": "RE3", "vehicle": {"type": "HEAVY_RAIL"}},
                        "departure_stop": {"name": "Prenzlau"},
                        "arrival_stop": {"name": "Berlin Hbf"},
                        "departure_time": {"value": 1743487260},
                        "arrival_time": {"value": 1743494460},
                    },
                },
                {
                    "travel_mode": "WALKING",
                    "duration": {"value": 120},
                },
                {
                    "travel_mode": "TRANSIT",
                    "transit_details": {
                        "line": {"short_name": "RE5", "vehicle": {"type": "HEAVY_RAIL"}},
                        "departure_stop": {"name": "Berlin Hbf"},
                        "arrival_stop": {"name": "Rostock Hbf"},
                        "departure_time": {"value": 1743495000},
                        "arrival_time": {"value": 1743502200},
                    },
                },
                {
                    "travel_mode": "TRANSIT",
                    "transit_details": {
                        "line": {"short_name": "S1", "vehicle": {"type": "COMMUTER_TRAIN"}},
                        "departure_stop": {"name": "Rostock Hbf"},
                        "arrival_stop": {"name": "Warnemünde"},
                        "departure_time": {"value": 1743502800},
                        "arrival_time": {"value": 1743504480},
                    },
                },
            ],
        }],
    }],
}

GMAPS_DIRECTIONS_NO_RESULTS = {
    "status": "ZERO_RESULTS",
    "routes": [],
}

GMAPS_GEOCODE_PRENZLAU = [{
    "place_id": "ChIJ_zNzWF9XqEcRMK_mzNPubgM",
    "formatted_address": "Prenzlau, Germany",
    "geometry": {"location": {"lat": 53.316, "lng": 13.863}},
}]

GMAPS_GEOCODE_WARNEMUENDE = [{
    "place_id": "ChIJH0sLz0p5sEcR_abc123",
    "formatted_address": "Rostock-Warnemünde, Germany",
    "geometry": {"location": {"lat": 54.170, "lng": 12.084}},
}]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clear_caches():
    """Clear all st.cache_data caches between tests."""
    yield
    # Import after path setup
    import db_client
    if hasattr(db_client, '_station_cache'):
        db_client._station_cache.clear()
    if hasattr(db_client, '_connection_cache'):
        db_client._connection_cache.clear()


class TestLookupStation:
    def test_resolves_station_name(self):
        mock_client = MagicMock()
        mock_client.geocode.return_value = GMAPS_GEOCODE_PRENZLAU

        with patch("db_client._gmaps", mock_client):
            from db_client import lookup_station
            result = lookup_station("Prenzlau")

        assert result is not None
        assert result["id"] == "ChIJ_zNzWF9XqEcRMK_mzNPubgM"
        assert "Prenzlau" in result["name"]

    def test_returns_none_for_unknown(self):
        mock_client = MagicMock()
        mock_client.geocode.return_value = []

        with patch("db_client._gmaps", mock_client):
            from db_client import lookup_station
            result = lookup_station("Nirgendwo Hbf")

        assert result is None


class TestFindConnection:
    def test_parses_transit_legs(self):
        mock_client = MagicMock()
        mock_client.directions.return_value = GMAPS_DIRECTIONS_PRENZLAU_WARNEMUENDE["routes"]

        with patch("db_client._gmaps", mock_client):
            from db_client import find_connection
            conn = find_connection(
                "ChIJ_prenzlau", "ChIJ_warnemuende",
                "2026-04-01T04:00:00",
            )

        assert conn is not None
        assert len(conn.legs) == 3  # RE3, RE5, S1 (walking skipped)
        assert conn.legs[0].line == "RE3"
        assert conn.legs[1].line == "RE5"
        assert conn.legs[2].line == "S1"
        assert conn.legs[0].departure_station == "Prenzlau"
        assert conn.legs[2].arrival_station == "Warnemünde"

    def test_returns_none_on_zero_results(self):
        mock_client = MagicMock()
        mock_client.directions.return_value = []

        with patch("db_client._gmaps", mock_client):
            from db_client import find_connection
            conn = find_connection("id_a", "id_b", "2026-04-01T04:00:00")

        assert conn is None


class TestCheckReachability:
    def test_reachable_when_arrival_before_deadline(self):
        mock_client = MagicMock()
        mock_client.directions.return_value = GMAPS_DIRECTIONS_PRENZLAU_WARNEMUENDE["routes"]

        with patch("db_client._gmaps", mock_client):
            from db_client import check_reachability_with_ids
            conn = check_reachability_with_ids(
                "id_prenzlau", "id_warnemuende",
                earliest_departure=datetime(2026, 4, 1, 4, 0),
                must_arrive_by=datetime(2026, 4, 1, 13, 14),  # Tour 704345 starts 13:19
            )

        assert conn is not None
        # Timestamps are interpreted as local time by datetime.fromtimestamp
        assert conn.arrival_time.hour == 12
        assert conn.arrival_time.minute == 48

    def test_not_reachable_when_arrival_after_deadline(self):
        mock_client = MagicMock()
        mock_client.directions.return_value = GMAPS_DIRECTIONS_PRENZLAU_WARNEMUENDE["routes"]

        with patch("db_client._gmaps", mock_client):
            from db_client import check_reachability_with_ids
            # Arrival is 2025-04-01 12:48 local; deadline before that
            conn = check_reachability_with_ids(
                "id_prenzlau", "id_warnemuende",
                earliest_departure=datetime(2025, 4, 1, 4, 0),
                must_arrive_by=datetime(2025, 4, 1, 6, 0),
            )

        assert conn is None

    def test_not_reachable_when_no_route(self):
        mock_client = MagicMock()
        mock_client.directions.return_value = []

        with patch("db_client._gmaps", mock_client):
            from db_client import check_reachability_with_ids
            conn = check_reachability_with_ids(
                "id_a", "id_b",
                earliest_departure=datetime(2026, 4, 1, 4, 0),
                must_arrive_by=datetime(2026, 4, 1, 23, 59),
            )

        assert conn is None


class TestStationsMatch:
    def test_exact_match(self):
        from db_client import stations_match
        assert stations_match("Prenzlau", "Prenzlau")

    def test_hbf_suffix(self):
        from db_client import stations_match
        assert stations_match("Rostock Hbf", "Rostock")

    def test_contained(self):
        from db_client import stations_match
        assert stations_match("Warnemünde", "Rostock-Warnemünde")

    def test_different_stations(self):
        from db_client import stations_match
        assert not stations_match("Berlin", "München")


class TestCrossBorderStations:
    """Regression: Stations in Poland/Czech Republic must be geocodable.

    Bug: lookup_station("Szczecin Glowny") appended ", Deutschland" to the
    geocode query. Since Szczecin is in Poland, Google returned a wrong
    German location (near Mühlhausen/Thuringia), causing a 5h43 detour
    via Berlin and Erfurt instead of a direct ~1.5h route.
    """

    def test_szczecin_glowny_query_not_restricted_to_germany(self):
        """Geocode queries for Szczecin Glowny must not contain 'Deutschland'."""
        mock_client = MagicMock()
        mock_client.geocode.return_value = [{
            "place_id": "ChIJ_szczecin_glowny",
            "formatted_address": "Szczecin Główny, Poland",
            "geometry": {"location": {"lat": 53.4285, "lng": 14.5528}},
        }]

        with patch("db_client._gmaps", mock_client):
            from db_client import lookup_station
            result = lookup_station("Szczecin Glowny")

        assert result is not None
        assert result["id"] == "ChIJ_szczecin_glowny"

        # Verify no query contained "Deutschland"
        for call in mock_client.geocode.call_args_list:
            query = call[0][0] if call[0] else call[1].get("address", "")
            assert "Deutschland" not in query, \
                f"Geocode query should not restrict to Deutschland: {query}"

    def test_swinoujscie_query_not_restricted_to_germany(self):
        """Swinoujscie Centrum is also in Poland — same fix needed."""
        mock_client = MagicMock()
        mock_client.geocode.return_value = [{
            "place_id": "ChIJ_swinoujscie",
            "formatted_address": "Świnoujście, Poland",
            "geometry": {"location": {"lat": 53.9108, "lng": 14.2471}},
        }]

        with patch("db_client._gmaps", mock_client):
            from db_client import lookup_station
            result = lookup_station("Swinoujscie Centrum")

        assert result is not None
        for call in mock_client.geocode.call_args_list:
            query = call[0][0] if call[0] else call[1].get("address", "")
            assert "Deutschland" not in query, \
                f"Geocode query should not restrict to Deutschland: {query}"

    def test_german_station_still_resolves(self):
        """German stations must still resolve correctly without Deutschland."""
        mock_client = MagicMock()
        mock_client.geocode.return_value = GMAPS_GEOCODE_PRENZLAU

        with patch("db_client._gmaps", mock_client):
            from db_client import lookup_station
            result = lookup_station("Prenzlau")

        assert result is not None
        assert "Prenzlau" in result["name"]
