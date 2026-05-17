# Auto-Discover Park Stations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **⛔ HARD RULE (project-specific, overrides skill defaults):** Do **NOT** run `git add`, `git commit`, or `git push` at any point during execution. The CLAUDE.md hard rule applies: only the human user authorizes git history changes, and only with the literal word "commit" or "push". Leave every task's changes as uncommitted working-tree modifications. Subagent prompts must explicitly include "do not commit".

**Goal:** Automatically discover train stations within driving radius of home and add them as park-station candidates, so the hybrid Anreise feature can use any nearby station (Pasewalk, Eberswalde, etc.) without it having to be a tour-departure station.

**Architecture:** New `nearby_park_stations()` in `transit_client.py` calls Google Maps Places Nearby Search, filters out S-Bahn-only stops by name pattern, filters by exact driving time, and returns top-15 sorted by drive_min. `optimize_with_modes` calls this once per session per (home, max_car_minutes) and forwards the list as a new `additional_park_stations` kwarg to `optimize_day_car_mode`, which unions it into the candidate-set.

**Tech Stack:** Python 3.12, Streamlit, googlemaps SDK (already a dependency), pytest, `uv`. The googlemaps SDK already exposes `places_nearby(...)` so no new dependency is needed.

**Spec:** `docs/superpowers/specs/2026-05-17-auto-discover-park-stations-design.md`

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `fahrtenplaner/transit_client.py` | modify | New `TransitClientError`, new module-level `_park_stations_cache`, new `_S_BAHN_NAME_PATTERN` + `_is_pure_sbahn_name`, new `nearby_park_stations(home_station, max_drive_minutes, top_k=15)`. |
| `fahrtenplaner/optimizer.py` | modify | New constant `AUTO_PARK_STATIONS_TOP_K`. `optimize_day_car_mode` accepts `additional_park_stations` kwarg, unions into `candidates`. `optimize_with_modes` calls `nearby_park_stations` and forwards. |
| `fahrtenplaner/ui/optimization.py` | modify | `_run_optimization`'s `except` branch detects `TransitClientError` and produces a specific error message via `report_error`. |
| `fahrtenplaner/ui/sidebar.py` | modify | Extend the "Max. Auto-Fahrzeit" slider's `help=` tooltip to mention that park-stations are auto-discovered and that pure S-Bahn stops are excluded. |
| `tests/test_transit_client.py` | modify | `TestNearbyParkStations` (6 tests). |
| `tests/test_optimizer.py` | modify | `TestOptimizeWithModesAutoPark` (4 tests). |

Baseline before this plan: 87 tests pass.

---

## Task 1: Add `TransitClientError` exception class

**Files:**
- Modify: `fahrtenplaner/transit_client.py`

Foundation for everything else. The new exception lets `nearby_park_stations` signal failures to the UI layer in a structured way (Places API quota / network / auth), distinct from "no results".

- [ ] **Step 1: Add the exception class at the top of `transit_client.py`**

Open `fahrtenplaner/transit_client.py`. Find the import block at the top. After the imports and before the `_gmaps` module-level variable (around line 18), insert:

```python
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
```

- [ ] **Step 2: Verify the module still imports**

Run: `uv run python -c "import sys; sys.path.insert(0, 'fahrtenplaner'); from transit_client import TransitClientError; print(TransitClientError.__name__)"`
Expected: prints `TransitClientError`.

---

## Task 2: Add S-Bahn name filter (TDD)

**Files:**
- Modify: `fahrtenplaner/transit_client.py`
- Modify: `tests/test_transit_client.py`

- [ ] **Step 1: Write the failing tests**

Open `tests/test_transit_client.py` and append:

```python
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
```

This test file uses `pytest.mark.parametrize`. Verify `pytest` is already imported at the top of `tests/test_transit_client.py`:

Run: `grep '^import pytest\|^from pytest' tests/test_transit_client.py`
Expected: shows `import pytest` (or `from pytest ...`). If missing, add `import pytest` at the top of the file.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --with pytest pytest tests/test_transit_client.py::TestSBahnNameFilter -v`
Expected: All parametrized cases FAIL with `ImportError: cannot import name '_is_pure_sbahn_name'`.

- [ ] **Step 3: Implement the filter in `transit_client.py`**

Add at the top of `transit_client.py`, near the other module-level constants (after `TransitClientError` from Task 1, before the existing `_gmaps` variable). First, add `import re` to the imports block if it's not already there:

Run: `grep '^import re' fahrtenplaner/transit_client.py`
- If empty: add `import re` to the import block (next to `import os`).
- If present: skip.

Then add the pattern and helper:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --with pytest pytest tests/test_transit_client.py::TestSBahnNameFilter -v`
Expected: All cases PASS (~18 total — 11 regional + 7 S-Bahn).

- [ ] **Step 5: Run the full suite**

Run: `uv run --with pytest pytest tests/ -q`
Expected: 87 + 18 = 105 tests passing (or whatever the existing baseline is plus the 18 new parametrized cases — pytest counts each parametrized case as one test).

---

## Task 3: Implement `nearby_park_stations` (TDD)

**Files:**
- Modify: `fahrtenplaner/transit_client.py`
- Modify: `tests/test_transit_client.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_transit_client.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --with pytest pytest tests/test_transit_client.py::TestNearbyParkStations -v`
Expected: All 6 FAIL with `ImportError: cannot import name 'nearby_park_stations'` (and on the home-not-geocodable case, `cannot import name '_park_stations_cache'`).

- [ ] **Step 3: Implement `_park_stations_cache` and `nearby_park_stations`**

Add to `fahrtenplaner/transit_client.py`. First, near the existing caches block (~line 56), add the new cache dict:

```python
# Module-level cache for nearby_park_stations. Key: (home_station_name,
# max_drive_minutes). Lifetime: same as the other caches — survives Streamlit
# reruns within one process, resets on worker restart.
_park_stations_cache: dict[tuple[str, int], list[str]] = {}
```

Then, somewhere after `driving_info` and `stations_match` are defined (the function uses both), add the discovery function:

```python
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

    candidates_with_time.sort(key=lambda pair: pair[0])
    park_stations = [name for _, name in candidates_with_time[:top_k]]

    _park_stations_cache[cache_key] = park_stations
    return park_stations
```

- [ ] **Step 4: Run the new tests**

Run: `uv run --with pytest pytest tests/test_transit_client.py::TestNearbyParkStations -v`
Expected: All 6 PASS.

- [ ] **Step 5: Run the full suite**

Run: `uv run --with pytest pytest tests/ -q`
Expected: Previous baseline + 18 (S-Bahn tests) + 6 (nearby tests) = baseline+24. Should match the count from Task 2 baseline plus 6.

---

## Task 4: Add `additional_park_stations` parameter to optimizer

**Files:**
- Modify: `fahrtenplaner/optimizer.py`

Pure plumbing — accept the parameter, union into the candidates set. No behavior change yet from the call-site side because nothing passes the kwarg.

- [ ] **Step 1: Add the constant**

In `fahrtenplaner/optimizer.py`, find the existing `HYBRID_MAX_API_LOOKUPS_PER_CANDIDATE` constant. Add right after it:

```python
# Auto-discovered park-stations cap: top-K nearby train stations (by driving
# time ascending) that are added to the car-mode candidate set. Each one
# costs one driving_info call and up to HYBRID_MAX_API_LOOKUPS_PER_CANDIDATE
# transit lookups during the hybrid pass.
AUTO_PARK_STATIONS_TOP_K: int = 15
```

- [ ] **Step 2: Add the kwarg to `optimize_day_car_mode`**

Find `optimize_day_car_mode` (~line 470). It currently looks like:

```python
def optimize_day_car_mode(
    tours: list[Tour],
    home_station: str,
    earliest_departure: datetime,
    latest_return: datetime,
    max_car_minutes: int,
    fuel_consumption: float,
    fuel_price: float,
    fuel_refund_per_km: float = 0.0,
    *,
    directly_reachable_tour_nrs: set[int] = frozenset(),
    progress_callback: ProgressCb = None,
    max_transfer_gap_hours: float = MAX_TRANSFER_GAP_HOURS,
) -> tuple[DayPlan, list[DayPlan]]:
```

Change to (add `additional_park_stations: list[str] = ()` between the existing kwargs):

```python
def optimize_day_car_mode(
    tours: list[Tour],
    home_station: str,
    earliest_departure: datetime,
    latest_return: datetime,
    max_car_minutes: int,
    fuel_consumption: float,
    fuel_price: float,
    fuel_refund_per_km: float = 0.0,
    *,
    directly_reachable_tour_nrs: set[int] = frozenset(),
    additional_park_stations: list[str] = (),
    progress_callback: ProgressCb = None,
    max_transfer_gap_hours: float = MAX_TRANSFER_GAP_HOURS,
) -> tuple[DayPlan, list[DayPlan]]:
```

- [ ] **Step 3: Union the kwarg into the candidates set**

Still inside `optimize_day_car_mode`, find the line that builds `candidates`:

```python
    # Candidate park-stations include every tour's departure station — even
    # tours that depart before `earliest_departure` (those can never anchor a
    # car chain themselves, but their station may still be a viable park spot
    # for *other* tours via the hybrid Anreise pass).
    candidates = sorted({t.departure_station for t in tours})
```

Change to:

```python
    # Candidate park-stations include every tour's departure station plus
    # any explicitly-discovered nearby park-stations (passed by the caller).
    # Even tours that depart before `earliest_departure` contribute their
    # station name (they can't anchor a car chain themselves, but their
    # station may still be a viable park spot for *other* tours via the
    # hybrid Anreise pass).
    candidates = sorted(
        {t.departure_station for t in tours}
        | set(additional_park_stations)
    )
```

- [ ] **Step 4: Run the full suite**

Run: `uv run --with pytest pytest tests/ -q`
Expected: Same count as end of Task 3. No behaviour change because nothing passes `additional_park_stations` yet.

---

## Task 5: Wire `nearby_park_stations` into `optimize_with_modes`

**Files:**
- Modify: `fahrtenplaner/optimizer.py`
- Modify: `tests/test_optimizer.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_optimizer.py`:

```python
# ---------------------------------------------------------------------------
# optimize_with_modes auto-discovers park-stations via Places API
# ---------------------------------------------------------------------------

class TestOptimizeWithModesAutoPark:
    """optimize_with_modes auto-calls nearby_park_stations when car-mode is
    active and forwards the result to optimize_day_car_mode as
    additional_park_stations."""

    def test_nearby_called_when_car_mode_active(self):
        from optimizer import optimize_with_modes
        from models import DayPlan

        with patch("optimizer.optimize_day",
                   return_value=(DayPlan(), [], set())), \
             patch("optimizer.stations_match", return_value=True), \
             patch("optimizer.nearby_park_stations",
                   return_value=["Pasewalk", "Angermünde"]) as nearby_mock, \
             patch("optimizer.optimize_day_car_mode",
                   return_value=(DayPlan(), [])):
            optimize_with_modes(
                tours=[],
                home_station="Prenzlau", dest_station="Prenzlau",
                earliest_departure=datetime.combine(DAY, time(4, 0)),
                latest_return=datetime.combine(DAY, time(23, 59)),
                max_car_minutes=30,
                fuel_consumption=7.0,
                fuel_price=1.79,
            )

        nearby_mock.assert_called_once()
        # max_drive_minutes positional or kwarg — accept either
        call = nearby_mock.call_args
        all_args = list(call.args) + list(call.kwargs.values())
        assert 30 in all_args

    def test_nearby_not_called_when_car_mode_off(self):
        """max_car_minutes=0 → no Places API call."""
        from optimizer import optimize_with_modes
        from models import DayPlan

        with patch("optimizer.optimize_day",
                   return_value=(DayPlan(), [], set())), \
             patch("optimizer.nearby_park_stations") as nearby_mock, \
             patch("optimizer.optimize_day_car_mode",
                   return_value=(DayPlan(), [])):
            optimize_with_modes(
                tours=[],
                home_station="Prenzlau", dest_station="Prenzlau",
                earliest_departure=datetime.combine(DAY, time(4, 0)),
                latest_return=datetime.combine(DAY, time(23, 59)),
                max_car_minutes=0,
                fuel_consumption=7.0,
                fuel_price=1.79,
            )
        nearby_mock.assert_not_called()

    def test_nearby_not_called_when_dest_differs(self):
        """Car-mode is only active when home==dest; otherwise the Places API
        is not consulted."""
        from optimizer import optimize_with_modes
        from models import DayPlan

        with patch("optimizer.optimize_day",
                   return_value=(DayPlan(), [], set())), \
             patch("optimizer.stations_match", return_value=False), \
             patch("optimizer.nearby_park_stations") as nearby_mock:
            optimize_with_modes(
                tours=[],
                home_station="Prenzlau", dest_station="Stralsund",
                earliest_departure=datetime.combine(DAY, time(4, 0)),
                latest_return=datetime.combine(DAY, time(23, 59)),
                max_car_minutes=30,
                fuel_consumption=7.0,
                fuel_price=1.79,
            )
        nearby_mock.assert_not_called()

    def test_discovered_stations_passed_to_car_mode(self):
        """The list returned by nearby_park_stations arrives at
        optimize_day_car_mode as additional_park_stations."""
        from optimizer import optimize_with_modes
        from models import DayPlan

        sentinel = ["Pasewalk", "Angermünde", "Eberswalde"]

        with patch("optimizer.optimize_day",
                   return_value=(DayPlan(), [], set())), \
             patch("optimizer.stations_match", return_value=True), \
             patch("optimizer.nearby_park_stations", return_value=sentinel), \
             patch("optimizer.optimize_day_car_mode",
                   return_value=(DayPlan(), [])) as car_mock:
            optimize_with_modes(
                tours=[],
                home_station="Prenzlau", dest_station="Prenzlau",
                earliest_departure=datetime.combine(DAY, time(4, 0)),
                latest_return=datetime.combine(DAY, time(23, 59)),
                max_car_minutes=30,
                fuel_consumption=7.0,
                fuel_price=1.79,
            )

        car_mock.assert_called_once()
        kwargs = car_mock.call_args.kwargs
        assert kwargs["additional_park_stations"] == sentinel
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --with pytest pytest tests/test_optimizer.py::TestOptimizeWithModesAutoPark -v`
Expected: All 4 FAIL because `optimizer.nearby_park_stations` is not yet imported and the wiring is missing.

- [ ] **Step 3: Import `nearby_park_stations` in `optimizer.py`**

At the top of `fahrtenplaner/optimizer.py`, find the import block. The file already imports from `transit_client`. Locate:

```python
from transit_client import (
    batch_lookup_stations,
    check_reachability_with_ids,
    driving_info,
    stations_match,
)
```

(or similar — the exact list may differ; preserve all existing imports). Add `nearby_park_stations` to the list:

```python
from transit_client import (
    batch_lookup_stations,
    check_reachability_with_ids,
    driving_info,
    nearby_park_stations,
    stations_match,
)
```

If the import is a flat `from transit_client import ...` on one line, expand it to multi-line as shown.

- [ ] **Step 4: Wire `nearby_park_stations` into `optimize_with_modes`**

Find `optimize_with_modes`. Locate the car-mode branch (it already wires `directly_reachable_tour_nrs`). Currently:

```python
    car_plan: DayPlan = DayPlan()
    car_candidates: list[DayPlan] = []
    if max_car_minutes > 0 and stations_match(home_station, dest_station):
        car_plan, car_candidates = optimize_day_car_mode(
            tours, home_station,
            earliest_departure, latest_return,
            max_car_minutes, fuel_consumption, fuel_price, fuel_refund_per_km,
            directly_reachable_tour_nrs=directly_reachable,
            progress_callback=progress_callback,
            max_transfer_gap_hours=max_transfer_gap_hours,
        )
```

Change to:

```python
    car_plan: DayPlan = DayPlan()
    car_candidates: list[DayPlan] = []
    if max_car_minutes > 0 and stations_match(home_station, dest_station):
        # Auto-discover nearby park-stations (Places API). Raises
        # TransitClientError on failure; caller (UI) catches it.
        park_stations = nearby_park_stations(
            home_station, max_drive_minutes=max_car_minutes,
            top_k=AUTO_PARK_STATIONS_TOP_K,
        )
        car_plan, car_candidates = optimize_day_car_mode(
            tours, home_station,
            earliest_departure, latest_return,
            max_car_minutes, fuel_consumption, fuel_price, fuel_refund_per_km,
            directly_reachable_tour_nrs=directly_reachable,
            additional_park_stations=park_stations,
            progress_callback=progress_callback,
            max_transfer_gap_hours=max_transfer_gap_hours,
        )
```

- [ ] **Step 5: Run the test class**

Run: `uv run --with pytest pytest tests/test_optimizer.py::TestOptimizeWithModesAutoPark -v`
Expected: 4 PASS.

- [ ] **Step 6: Run the full suite**

Run: `uv run --with pytest pytest tests/ -q`
Expected: Previous baseline + 4 new tests. No existing test regressions (the existing `TestCarMode` and `TestHybrid*` tests mock `optimize_day` and `optimize_day_car_mode` at the module level; they don't go through the production wiring, so the new `nearby_park_stations` call doesn't fire there).

If a previously-passing test now fails because it goes through `optimize_with_modes` and trips on `nearby_park_stations` (without mocking it), patch the test to also mock `optimizer.nearby_park_stations` returning `[]`. **Most likely candidate**: `TestOptimizeWithModesHybrid::test_end_to_end_pasewalk_scenario` already passes mocks for everything else; it may need the additional `patch("optimizer.nearby_park_stations", return_value=[])` to prevent the production call.

Specifically, in `tests/test_optimizer.py` if `test_end_to_end_pasewalk_scenario` fails, change its `with patch(...)` block to add `patch("optimizer.nearby_park_stations", return_value=[])`:

```python
        with patch("optimizer.batch_lookup_stations", return_value=geocode), \
             patch("optimizer.driving_info", return_value=(30, 32.0)), \
             patch("optimizer.check_reachability_with_ids",
                   side_effect=reachability_router), \
             patch("optimizer.nearby_park_stations", return_value=[]):
            result = optimize_with_modes(...)
```

Run the full suite again after the patch:
`uv run --with pytest pytest tests/ -q`
Expected: All tests pass.

---

## Task 6: UI error handling — `TransitClientError` dialog

**Files:**
- Modify: `fahrtenplaner/ui/optimization.py`

- [ ] **Step 1: Locate the existing error block**

Open `fahrtenplaner/ui/optimization.py` and find `_run_optimization` (~line 113). Inside, there is an `except` block that catches `Exception` and routes to `report_error`. It looks roughly like:

```python
    opt_exc: Exception | None = None
    try:
        result = optimize_with_modes(...)
    except Exception as e:
        result = OptimizationResult(winner=DayPlan(), alternative=None)
        opt_exc = e
```

…and later:

```python
    if opt_exc is not None:
        report_error(
            "Optimierung fehlgeschlagen",
            details=(
                ...
            ),
            exc=opt_exc,
        )
```

- [ ] **Step 2: Add `TransitClientError` import**

At the top of `fahrtenplaner/ui/optimization.py`, find the imports from `transit_client` (likely there are none currently — UI imports from `models` and `optimizer`). Add:

```python
from transit_client import TransitClientError
```

(If `transit_client` is not yet imported at all in this file, add it as a new line in the imports block.)

- [ ] **Step 3: Branch the error reporter on the exception type**

In the section that calls `report_error`, change:

```python
    if opt_exc is not None:
        report_error(
            "Optimierung fehlgeschlagen",
            details=(
                f"Datum: {ctx.selected_date.strftime('%d.%m.%Y')}\n"
                f"Route: {ctx.home_station} → "
                f"{ctx.home_station if ctx.same_station else ctx.dest_station}\n"
                f"Touren am Tag: {len(day_tours)}\n"
                f"Fenster: {dep_time:%H:%M}–{ret_time:%H:%M}, "
                f"max. Pause: {max_gap_minutes} Min, max. Auto: {max_car_minutes} Min\n"
                f"Letzter Schritt: {(log_messages[-1] if log_messages else '—')}"
            ),
            exc=opt_exc,
        )
```

to:

```python
    if opt_exc is not None:
        if isinstance(opt_exc, TransitClientError):
            report_error(
                "Park-Bahnhof-Suche fehlgeschlagen",
                details=(
                    f"Beim Suchen nach Park-Bahnhöfen im Umkreis von "
                    f"{max_car_minutes} Min hat Google Maps Places einen "
                    f"Fehler gemeldet.\n\n"
                    f"Tipp: Auto-Anfahrt vorübergehend ausschalten "
                    f"(Schieber auf 0) und nur die Tour-Bahnhöfe als "
                    f"Park-Optionen verwenden, oder gleich nochmal versuchen."
                ),
                exc=opt_exc,
            )
        else:
            report_error(
                "Optimierung fehlgeschlagen",
                details=(
                    f"Datum: {ctx.selected_date.strftime('%d.%m.%Y')}\n"
                    f"Route: {ctx.home_station} → "
                    f"{ctx.home_station if ctx.same_station else ctx.dest_station}\n"
                    f"Touren am Tag: {len(day_tours)}\n"
                    f"Fenster: {dep_time:%H:%M}–{ret_time:%H:%M}, "
                    f"max. Pause: {max_gap_minutes} Min, max. Auto: {max_car_minutes} Min\n"
                    f"Letzter Schritt: {(log_messages[-1] if log_messages else '—')}"
                ),
                exc=opt_exc,
            )
```

- [ ] **Step 4: Smoke-check that the UI module still imports**

Run: `uv run python -c "import sys; sys.path.insert(0, 'fahrtenplaner'); from ui import optimization; print('ok')"`
Expected: prints `ok` with no exception.

- [ ] **Step 5: Run the full test suite as a safety net**

Run: `uv run --with pytest pytest tests/ -q`
Expected: All tests still pass. UI is not test-covered; this just confirms no backend regression slipped in.

---

## Task 7: Add UI hint that S-Bahn-only stops are excluded

**Files:**
- Modify: `fahrtenplaner/ui/sidebar.py`

Tiny UX touch — extend the existing tooltip on the "Max. Auto-Fahrzeit" slider so users discover that auto-discovery runs and that pure S-Bahn stops are not considered. No new UI element; one help-string extended.

- [ ] **Step 1: Locate the slider**

Open `fahrtenplaner/ui/sidebar.py` and find `_render_auto_panel` (~line 208). Inside, locate the slider widget:

```python
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
```

- [ ] **Step 2: Extend the help string**

Change the `help=` argument so the active-mode branch mentions auto-discovery and the S-Bahn exclusion:

```python
        max_minutes = st.slider(
            "Max. Auto-Fahrzeit (Min.)",
            min_value=0,
            max_value=120,
            step=5,
            value=int(st.session_state.max_car_minutes),
            disabled=not same_station,
            help=(
                "Wie weit dürft ihr morgens mit dem Auto fahren, um den "
                "Startbahnhof zu erreichen? 0 = ausschließlich ÖPNV. "
                "Park-Bahnhöfe werden automatisch im Umkreis gesucht — "
                "reine S-Bahn-Stops bleiben unberücksichtigt."
                if same_station
                else "Auto-Modus erfordert Ankunft = Abfahrt."
            ),
        )
```

- [ ] **Step 3: Smoke-check that the module still imports**

Run: `uv run python -c "import sys; sys.path.insert(0, 'fahrtenplaner'); from ui import sidebar; print('ok')"`
Expected: prints `ok`.

- [ ] **Step 4: Run the full test suite as a safety net**

Run: `uv run --with pytest pytest tests/ -q`
Expected: All tests still pass.

---

## Final Verification

- [ ] **Run the full suite one more time**

Run: `uv run --with pytest pytest tests/ -v`
Expected: All previously-passing tests plus the new ones (≈ 87 baseline + 18 S-Bahn-parametrized + 6 nearby + 4 optimize-with-modes = 115 give-or-take depending on parametrize counting).

- [ ] **Manual smoke-test in the browser**

Run: `./dev.sh`

In the browser:
1. Pick 2026-06-01, Brandenburg + Mecklenburg-Vorpommern, "Touren laden".
2. Heimatbahnhof: Prenzlau, "Ankunft = Abfahrt" on.
3. Auto-Anfahrt ≤ 60 min.
4. Frühste Abfahrt: 03:00 (give Pasewalk hybrid Anreise room to breathe).
5. Click "Optimale Route berechnen".

Watch the optimization-details log for the auto-discovered park-stations (the log captures progress messages; no special log entry exists, but if the Places API found stations, hybrid plans will surface). Verify Tour 721174 Stralsund → Angermünde now appears either as the winner or in "Weitere Optionen", with a chain like `🚗 Prenzlau→Pasewalk · 🚉 Pasewalk→Stralsund Hbf · Tour · 🚆 Angermünde→Pasewalk · 🚗 Pasewalk→Prenzlau`.

- [ ] **Hand back to the user**

Report what changed, list the modified files, do **not** commit. The user reviews the working tree and runs `git commit` themselves.
