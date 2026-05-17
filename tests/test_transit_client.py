"""Tests for transit_client Google Maps integration — mocked, no real API calls."""

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
    import transit_client
    if hasattr(transit_client, '_station_cache'):
        transit_client._station_cache.clear()
    if hasattr(transit_client, '_connection_cache'):
        transit_client._connection_cache.clear()
    if hasattr(transit_client, '_driving_cache'):
        transit_client._driving_cache.clear()


class TestLookupStation:
    def test_resolves_station_name(self):
        mock_client = MagicMock()
        mock_client.geocode.return_value = GMAPS_GEOCODE_PRENZLAU

        with patch("transit_client._gmaps", mock_client):
            from transit_client import lookup_station
            result = lookup_station("Prenzlau")

        assert result is not None
        assert result["id"] == "ChIJ_zNzWF9XqEcRMK_mzNPubgM"
        assert "Prenzlau" in result["name"]

    def test_returns_none_for_unknown(self):
        mock_client = MagicMock()
        mock_client.geocode.return_value = []

        with patch("transit_client._gmaps", mock_client):
            from transit_client import lookup_station
            result = lookup_station("Nirgendwo Hbf")

        assert result is None


class TestFindConnection:
    def test_parses_transit_legs(self):
        mock_client = MagicMock()
        mock_client.directions.return_value = GMAPS_DIRECTIONS_PRENZLAU_WARNEMUENDE["routes"]

        with patch("transit_client._gmaps", mock_client):
            from transit_client import find_connection
            conn = find_connection(
                "ChIJ_prenzlau", "ChIJ_warnemuende",
                "2026-04-01T04:00:00",
            )

        assert conn is not None
        # Walking leg is now included between the RE3 and RE5 transits.
        assert len(conn.legs) == 4  # RE3, Fußweg, RE5, S1
        assert conn.legs[0].line == "RE3"
        assert conn.legs[1].line == "🚶 Fußweg"
        assert conn.legs[2].line == "RE5"
        assert conn.legs[3].line == "S1"
        assert conn.legs[0].departure_station == "Prenzlau"
        assert conn.legs[3].arrival_station == "Warnemünde"
        # The walking step's start/end are the surrounding transit stops.
        assert conn.legs[1].departure_station == "Berlin Hbf"
        assert conn.legs[1].arrival_station == "Berlin Hbf"

    def test_returns_none_on_zero_results(self):
        mock_client = MagicMock()
        mock_client.directions.return_value = []

        with patch("transit_client._gmaps", mock_client):
            from transit_client import find_connection
            conn = find_connection("id_a", "id_b", "2026-04-01T04:00:00")

        assert conn is None


class TestCheckReachability:
    def test_reachable_when_arrival_before_deadline(self):
        mock_client = MagicMock()
        mock_client.directions.return_value = GMAPS_DIRECTIONS_PRENZLAU_WARNEMUENDE["routes"]

        with patch("transit_client._gmaps", mock_client):
            from transit_client import check_reachability_with_ids
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

        with patch("transit_client._gmaps", mock_client):
            from transit_client import check_reachability_with_ids
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

        with patch("transit_client._gmaps", mock_client):
            from transit_client import check_reachability_with_ids
            conn = check_reachability_with_ids(
                "id_a", "id_b",
                earliest_departure=datetime(2026, 4, 1, 4, 0),
                must_arrive_by=datetime(2026, 4, 1, 23, 59),
            )

        assert conn is None


class TestStationsMatch:
    def test_exact_match(self):
        from transit_client import stations_match
        assert stations_match("Prenzlau", "Prenzlau")

    def test_hbf_suffix(self):
        from transit_client import stations_match
        assert stations_match("Rostock Hbf", "Rostock")

    def test_contained(self):
        from transit_client import stations_match
        assert stations_match("Warnemünde", "Rostock-Warnemünde")

    def test_different_stations(self):
        from transit_client import stations_match
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

        with patch("transit_client._gmaps", mock_client):
            from transit_client import lookup_station
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

        with patch("transit_client._gmaps", mock_client):
            from transit_client import lookup_station
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

        with patch("transit_client._gmaps", mock_client):
            from transit_client import lookup_station
            result = lookup_station("Prenzlau")

        assert result is not None
        assert "Prenzlau" in result["name"]


class TestDrivingInfo:
    """driving_info returns (minutes, km) from a mocked Google Directions response."""

    def test_returns_minutes_and_km(self):
        # Mocked Directions response: 30-minute, 32-km drive
        mock_response = [{
            "legs": [{
                "duration": {"value": 1800},   # seconds → 30 min
                "distance": {"value": 32000},  # meters → 32 km
            }]
        }]
        mock_client = MagicMock()
        mock_client.directions.return_value = mock_response

        with patch("transit_client._gmaps", mock_client):
            from transit_client import driving_info
            result = driving_info("ChIJ_home", "ChIJ_target")

        assert result is not None
        minutes, km = result
        assert minutes == 30
        assert km == pytest.approx(32.0, abs=0.01)

    def test_caches_result_so_second_call_skips_api(self):
        """The second call with the same place_ids does not hit the API."""
        mock_response = [{
            "legs": [{
                "duration": {"value": 1800},
                "distance": {"value": 32000},
            }]
        }]
        mock_client = MagicMock()
        mock_client.directions.return_value = mock_response

        with patch("transit_client._gmaps", mock_client):
            from transit_client import driving_info
            first = driving_info("ChIJ_cache_a", "ChIJ_cache_b")
            second = driving_info("ChIJ_cache_a", "ChIJ_cache_b")

        assert first == second
        # Cache should mean only ONE actual API call despite two function calls.
        assert mock_client.directions.call_count == 1


# ---------------------------------------------------------------------------
# S-Bahn name filter — used by nearby_park_stations to drop S-Bahn-only stops
# ---------------------------------------------------------------------------

class TestSBahnNameFilter:
    """Conservative S-Bahn name filter: must not accidentally filter cities
    that happen to start with the letter S."""

    @pytest.mark.parametrize("name", [
        "Stralsund Hauptbahnhof",
        "Stralsund Hbf",
        "Senftenberg",
        "Schwedt (Oder)",
        "Spandau",
        "Berlin Friedrichstraße",
        "Berlin Spandau",
        "Südkreuz",
        "Stendal",
        "Schöneweide",
        "Pasewalk",
    ])
    def test_keeps_regional_stations(self, name: str):
        from transit_client import _is_pure_sbahn_name
        assert _is_pure_sbahn_name(name) is False

    @pytest.mark.parametrize("name", [
        "S Wedding",
        "S Friedrichstraße",
        "S+U Berlin Friedrichstraße",
        "S+U Hauptbahnhof",
        "S-Bahnhof Schöneberg",
        "S-Bahn Wedding",
        "Berlin (S)",
    ])
    def test_drops_pure_sbahn_stops(self, name: str):
        from transit_client import _is_pure_sbahn_name
        assert _is_pure_sbahn_name(name) is True


# ---------------------------------------------------------------------------
# nearby_park_stations — Places API + driving-radius filter + S-Bahn drop
# ---------------------------------------------------------------------------

class TestNearbyParkStations:
    """Discover train stations within driving radius of home.

    All tests mock at the transit_client module level so the actual Google
    Maps SDK is never invoked. Each test resets the module-level
    _park_stations_cache so cache hits don't leak between tests.
    """

    def setup_method(self):
        # Clear all module-level caches so each test starts fresh
        from transit_client import (
            _park_stations_cache, _station_cache, _driving_cache,
        )
        _park_stations_cache.clear()
        _station_cache.clear()
        _driving_cache.clear()

    def _places_response(self, *names: str) -> dict:
        """Build a Google Places API nearby-search-style response."""
        return {
            "status": "OK",
            "results": [
                {
                    "name": name,
                    "place_id": f"pid_{name.replace(' ', '_')}",
                    "geometry": {"location": {"lat": 53.0, "lng": 13.5}},
                    "types": ["train_station"],
                }
                for name in names
            ],
        }

    def test_returns_filtered_sorted_truncated(self):
        """Places returns 8 stations; 2 are S-Bahn → dropped by name filter.
        Remaining 6 are filtered to those within 60min by driving_info; 4
        survive that. Result is sorted by drive_min asc and truncated to
        top_k=3 for this test."""
        from transit_client import nearby_park_stations

        places = self._places_response(
            "Angermünde", "Pasewalk", "Eberswalde", "Templin",
            "Schwedt (Oder)", "Neubrandenburg",
            "S Wedding",            # filtered by name
            "S+U Friedrichstraße",  # filtered by name
        )
        # drive_min for each (None means: not reachable / outside radius)
        drive_minutes = {
            "Angermünde": 30, "Pasewalk": 35, "Eberswalde": 45,
            "Templin": 50, "Schwedt (Oder)": None, "Neubrandenburg": 75,
        }
        # lookup_station returns a stub with id + location
        def lookup_side_effect(name):
            return {"id": f"pid_{name}", "name": name,
                    "location": {"lat": 53.0, "lng": 13.5}}
        # driving_info(from_id=home, to_id=station_id) returns minutes from map
        def driving_side_effect(from_id, to_id):
            # to_id is "pid_<name>"
            name = to_id[4:]
            mins = drive_minutes.get(name)
            return (mins, 30.0) if mins else None

        with patch("transit_client.lookup_station",
                   side_effect=lookup_side_effect), \
             patch("transit_client.driving_info",
                   side_effect=driving_side_effect), \
             patch("transit_client._get_client") as mock_client:
            mock_client.return_value.places_nearby.return_value = places
            result = nearby_park_stations(
                home_station="Prenzlau",
                max_drive_minutes=60,
                top_k=3,
            )

        # Schwedt drops (driving_info returned None).
        # Neubrandenburg drops (75 > 60).
        # 4 survivors sorted asc by drive_min: Angermünde, Pasewalk, Eberswalde, Templin.
        # Truncated to top 3.
        assert result == ["Angermünde", "Pasewalk", "Eberswalde"]

    def test_skips_home_station_itself(self):
        """If Places returns the home station as a result, it is dropped."""
        from transit_client import nearby_park_stations

        places = self._places_response("Prenzlau", "Pasewalk")

        def lookup_side_effect(name):
            return {"id": f"pid_{name}", "name": name,
                    "location": {"lat": 53.0, "lng": 13.5}}

        with patch("transit_client.lookup_station",
                   side_effect=lookup_side_effect), \
             patch("transit_client.driving_info", return_value=(30, 30.0)), \
             patch("transit_client._get_client") as mock_client:
            mock_client.return_value.places_nearby.return_value = places
            result = nearby_park_stations(
                home_station="Prenzlau",
                max_drive_minutes=60,
                top_k=15,
            )

        assert "Prenzlau" not in result
        assert result == ["Pasewalk"]

    def test_cache_hit_skips_places_call(self):
        """A second call with same args returns the cached list without
        re-invoking places_nearby."""
        from transit_client import nearby_park_stations

        places = self._places_response("Pasewalk")

        def lookup_side_effect(name):
            return {"id": f"pid_{name}", "name": name,
                    "location": {"lat": 53.0, "lng": 13.5}}

        with patch("transit_client.lookup_station",
                   side_effect=lookup_side_effect), \
             patch("transit_client.driving_info", return_value=(30, 30.0)), \
             patch("transit_client._get_client") as mock_client:
            mock_client.return_value.places_nearby.return_value = places
            # First call: hits the API
            nearby_park_stations("Prenzlau", 60, top_k=15)
            # Second call: cache hit
            nearby_park_stations("Prenzlau", 60, top_k=15)
            # places_nearby should only have been called once.
            assert mock_client.return_value.places_nearby.call_count == 1

    def test_different_max_drive_minutes_is_separate_cache_entry(self):
        """Calls with different max_drive_minutes don't share cache entries."""
        from transit_client import nearby_park_stations

        places = self._places_response("Pasewalk")

        def lookup_side_effect(name):
            return {"id": f"pid_{name}", "name": name,
                    "location": {"lat": 53.0, "lng": 13.5}}

        with patch("transit_client.lookup_station",
                   side_effect=lookup_side_effect), \
             patch("transit_client.driving_info", return_value=(30, 30.0)), \
             patch("transit_client._get_client") as mock_client:
            mock_client.return_value.places_nearby.return_value = places
            nearby_park_stations("Prenzlau", 30, top_k=15)
            nearby_park_stations("Prenzlau", 60, top_k=15)
            # places_nearby called twice — once per unique (home, minutes) key.
            assert mock_client.return_value.places_nearby.call_count == 2

    def test_raises_transit_client_error_on_places_failure(self):
        """If places_nearby raises, nearby_park_stations wraps it in
        TransitClientError so the UI can render a specific dialog."""
        from transit_client import nearby_park_stations, TransitClientError

        with patch("transit_client.lookup_station",
                   return_value={"id": "pid_home", "name": "Prenzlau",
                                  "location": {"lat": 53.0, "lng": 13.5}}), \
             patch("transit_client._get_client") as mock_client:
            mock_client.return_value.places_nearby.side_effect = (
                RuntimeError("quota exceeded")
            )
            with pytest.raises(TransitClientError):
                nearby_park_stations("Prenzlau", 60, top_k=15)

    def test_returns_empty_when_home_not_geocodable(self):
        """If lookup_station returns None for the home station, we raise
        TransitClientError because we cannot proceed."""
        from transit_client import nearby_park_stations, TransitClientError

        with patch("transit_client.lookup_station", return_value=None):
            with pytest.raises(TransitClientError):
                nearby_park_stations("UnknownTown", 60, top_k=15)
