# Auto-Discover Park Stations — Design Spec

Date: 2026-05-17
Author: brainstormed with user (Sebastian + Papa)

## Problem

The hybrid car+transit Anreise feature (implemented in
`docs/superpowers/specs/2026-05-17-hybrid-car-transit-anreise-design.md`)
generalises car-mode so the tour does not have to start at the park-station.
But the candidate park-stations are still drawn exclusively from tour
departure stations: `candidates = sorted({t.departure_station for t in
tours})` in `optimize_day_car_mode`. This means stations like Pasewalk —
which are useful as park-and-ride spots between home and a far-away
tour-start, but which are not themselves the start of any tour on a given
day — are invisible to the optimizer.

Concrete example: 2026-06-01 from Prenzlau, tour 721174 (Stralsund Hbf
06:12 → Angermünde 08:25, 42.12 €). No tour in the day starts at Pasewalk,
so Pasewalk is not in the candidate set. The user can drive to Pasewalk in
~30 min and reach Stralsund Hbf by RE3 in time, but the optimizer never
considers this option.

## Goal

Automatically discover all relevant train stations within driving radius of
home and add them to the car-mode candidate set. The user does not need to
configure anything — switching on Auto-Anfahrt is enough.

## Non-goals

- No manual station list in the UI. The whole point of this feature is
  auto-discovery.
- No discovery of stations beyond the existing `max_car_minutes` setting.
  The setting still governs how far the user is willing to drive.
- No new UI surfaces for the user to see the discovered park stations. The
  hybrid plans that use them naturally surface via the existing winner /
  alternative / efficiency-options sections.
- Do not modify how `_build_car_chain_for_candidate` works internally. The
  hybrid pass from the previous spec already handles "tour does not start
  at candidate" correctly. We only widen the candidate-set feeding into it.

## Architecture

Three layers change. The Google Maps API adds Places Nearby Search on top of
the existing Geocoding and Directions calls.

### `fahrtenplaner/transit_client.py`

A new module-level function and cache, following the existing pattern (the
file already has `_station_cache`, `_connection_cache`, `_gmaps` singletons
that survive across Streamlit reruns):

```python
import re

# Module-level cache: home-station-name → list of nearby park-station names.
# One Places API call per unique (home, max_drive_minutes) tuple per session.
_park_stations_cache: dict[tuple[str, int], list[str]] = {}

# Conservative S-Bahn name filter — only matches unambiguous S-Bahn-only
# naming patterns. False negatives (S-Bahn slipping through) are fine: the
# hybrid pass naturally drops them because no Regionalbahn connection
# exists. False positives (filtering a real Regio station) would be bad.
_S_BAHN_NAME_PATTERN: re.Pattern[str] = re.compile(
    r"^(S\s|S\+)"           # "S Wedding", "S+U Friedrichstraße"
    r"|\bS-Bahn(hof)?\b"    # "S-Bahn Köpenick", "S-Bahnhof Wedding"
    r"|\s\(S\)$"            # "Berlin (S)" — some data sources
)


def _is_pure_sbahn_name(name: str) -> bool:
    """True iff the station name unambiguously looks like an S-Bahn-only stop."""
    return bool(_S_BAHN_NAME_PATTERN.search(name))


def nearby_park_stations(
    home_station: str,
    max_drive_minutes: int,
    top_k: int = 15,
) -> list[str]:
    """Discover train stations within driving radius of home.

    Returns a list of station names (not place IDs), suitable for passing
    into the optimizer's existing geocoding+driving pipeline. Sorted by
    driving time ascending; truncated to `top_k`. S-Bahn-only stops are
    filtered out by name pattern.

    The first call for a given (home, max_drive_minutes) pair makes one
    Places Nearby Search API call plus one driving_info call per result;
    subsequent calls return the cached list. Cache is module-level (resets
    on Streamlit worker restart, intentional — same lifetime as the other
    caches in this file).

    Raises:
        TransitClientError: if the Places API call fails (network, quota,
        auth). The UI catches this and surfaces it via report_error.
    """
```

The radius passed to Places API: `radius_km = min(50, max_drive_minutes)`.
Rationale: Places API caps at 50 km. With ~60 km/h secondary-road speed,
60 min ≈ 60 km Luftlinie, but the API ceiling cuts it to 50. The subsequent
`driving_info` filter applies the exact `max_drive_minutes` constraint
precisely.

Implementation order inside `nearby_park_stations`:

1. Cache check on `(home_station, max_drive_minutes)` — return cached if hit.
2. Geocode `home_station` (uses existing `lookup_station` + cache).
3. `gmaps.places_nearby(location=home_latlng, radius=radius_km * 1000,
   type="train_station")` — returns up to 20 results per page; we don't
   paginate (single page is more than enough for our use).
4. Extract names from results, filter out anything matching
   `_is_pure_sbahn_name`.
5. For each surviving station name, call `driving_info(home_id, station_id)`
   to get exact drive time. Drop if `> max_drive_minutes` or if `None`.
6. Sort surviving stations by drive_min ascending.
7. Truncate to `top_k = 15` (configurable via constant in `optimizer.py`).
8. Cache and return.

`places_nearby` failures (network error, missing API key, quota exhausted)
raise a new `TransitClientError` exception that propagates to the UI layer.
This is distinct from "no nearby stations found" which legitimately returns
`[]`.

### `fahrtenplaner/optimizer.py`

**New constant** alongside the existing `HYBRID_*`:

```python
# Top-K park-station candidates auto-discovered around home. Higher → wider
# search but more API calls. Includes only stations within max_car_minutes
# driving radius (filtered exactly by driving_info before truncation).
AUTO_PARK_STATIONS_TOP_K: int = 15
```

**`optimize_day_car_mode`** — accept a new keyword-only parameter holding
the pre-discovered list, and merge it into the candidate-set:

```python
def optimize_day_car_mode(
    tours, home_station, earliest_departure, latest_return,
    max_car_minutes, fuel_consumption, fuel_price, fuel_refund_per_km=0.0,
    *,
    directly_reachable_tour_nrs: set[int] = frozenset(),
    additional_park_stations: list[str] = (),     # NEW
    progress_callback=None,
    max_transfer_gap_hours=MAX_TRANSFER_GAP_HOURS,
) -> tuple[DayPlan, list[DayPlan]]:
    ...
    candidates = sorted(
        {t.departure_station for t in tours}
        | set(additional_park_stations)
    )
    ...
```

Default `()` (empty tuple) — backwards compatible with callers that omit
the kwarg. Tests written for the hybrid feature continue to pass with no
change.

**`optimize_with_modes`** — orchestrates the discovery call and passes the
result down. The discovery happens **only** when car-mode is active
(`max_car_minutes > 0` and `stations_match(home, dest)`):

```python
def optimize_with_modes(...):
    transit_plan, transit_candidates, directly_reachable = optimize_day(...)

    car_plan: DayPlan = DayPlan()
    car_candidates: list[DayPlan] = []
    if max_car_minutes > 0 and stations_match(home_station, dest_station):
        # Auto-discover park-stations within driving radius. Raises
        # TransitClientError on Places API failure (caught by UI).
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
    ...
```

### `fahrtenplaner/ui/optimization.py`

The existing `_run_optimization` function already wraps `optimize_with_modes`
in a try/except that routes any `Exception` to `report_error` via the
dialog flow. The new `TransitClientError` (from `nearby_park_stations`) is
caught by the same handler. **No UI code change required** — only the error
message text in `report_error` may want enrichment to make Places-API
failures distinguishable from MyRES failures.

We add one targeted clarification: the `except` clause already classifies
failure modes; we add a sub-branch that detects `TransitClientError` (or
any error originating from the Places call) and produces a more specific
title: "Park-Bahnhof-Suche fehlgeschlagen" instead of the generic
"Optimierung fehlgeschlagen". Body text explains: Auto-Anfahrt deactivating
or retrying may help.

### Tests

`tests/test_transit_client.py` gets a new test class
`TestNearbyParkStations` covering the new function:

- `test_returns_filtered_park_stations` — Places API mock returns 8
  stations including 2 S-Bahn-named ones; result excludes the 2 S-Bahn,
  sorted by drive_min asc, truncated to top_k.
- `test_sbahn_name_filter_preserves_cities_starting_with_s` — explicitly
  checks that "Stralsund", "Senftenberg", "Schwedt (Oder)", "Spandau",
  "Südkreuz", "Stendal" are NOT filtered.
- `test_sbahn_name_filter_drops_pure_sbahn_stops` — explicitly checks that
  "S Wedding", "S+U Friedrichstraße", "S-Bahnhof Köpenick", "Berlin (S)"
  ARE filtered.
- `test_cache_hit_skips_places_call` — second call with same args does not
  invoke `places_nearby` again (asserted via call_count).
- `test_drops_stations_outside_drive_radius` — Places returns 5 stations,
  driving_info reports 3 within max_drive_minutes and 2 outside → only the
  3 within are kept.
- `test_raises_on_places_api_failure` — `places_nearby` raises generic
  Exception; `nearby_park_stations` wraps it in `TransitClientError`.

`tests/test_optimizer.py` adds `TestOptimizeWithModesAutoPark`:

- `test_nearby_park_stations_called_when_car_mode_enabled` — verifies
  `optimize_with_modes` invokes `nearby_park_stations` exactly once when
  `max_car_minutes > 0` and home==dest.
- `test_nearby_park_stations_not_called_when_car_mode_disabled` —
  `max_car_minutes=0` → no Places API call.
- `test_discovered_stations_passed_to_car_mode` — sentinel list from mock
  appears in `optimize_day_car_mode.call_args.kwargs["additional_park_stations"]`.
- `test_end_to_end_pasewalk_auto_discovered` — analog to the previous
  hybrid integration test but without the decoy tour: Pasewalk is
  discovered automatically via the mocked Places API and the chain
  `car_outbound → outbound → tour → inbound → car_inbound` is built.

`tests/test_optimizer.py` existing tests that mock
`optimize_day_car_mode` need a one-line check: their mocks still need to
accept the new `additional_park_stations` kwarg implicitly (default empty
tuple makes this seamless). No changes to those tests.

Tests for Places-API-failure routing through the UI dialog are not in scope
(UI is not test-covered). The unit test for `TransitClientError` suffices.

## Data flow

```
optimize_with_modes (when car-mode active)
├── optimize_day                                  → (winner, candidates, directly_reachable)
├── nearby_park_stations(home, max_drive_minutes) → list[str]
│   ├── cache check on (home, max_drive_minutes)
│   ├── geocode home → (lat, lng)
│   ├── gmaps.places_nearby(home_latlng, radius_km=min(50, max_car_minutes),
│   │                       type="train_station")
│   ├── filter: drop name matching _S_BAHN_NAME_PATTERN
│   ├── for each survivor: driving_info(home, station)
│   │   keep if drive_min ≤ max_drive_minutes
│   ├── sort by drive_min asc, truncate to AUTO_PARK_STATIONS_TOP_K
│   └── cache and return
├── optimize_day_car_mode(..., additional_park_stations=park_stations,
│                              directly_reachable_tour_nrs=directly_reachable)
│   candidates = tour-departures ∪ additional_park_stations  (NEW)
│   for each candidate:
│     _build_car_chain_for_candidate(...)   # unchanged, hybrid seed handles it
└── _build_efficiency_options(...)          # unchanged
```

## Edge cases

- **No nearby stations found.** `nearby_park_stations` returns `[]`,
  candidates set falls back to tour-departures only. Behaviour identical
  to today.
- **Home station not geocodable.** `lookup_station` returns `None`; we
  raise `TransitClientError` immediately. Optimization aborts. (This is
  the same failure shape as today's geocoding-failed path; the user
  already sees an error for unreachable home anyway.)
- **All discovered stations are S-Bahn.** Filter removes everything;
  result list is `[]`. Behaviour identical to "no stations found".
- **Discovered station is already a tour-departure.** `candidates = set(...)`
  deduplicates naturally.
- **Places API returns stations whose names don't match any DB station ID
  the rest of the pipeline can geocode.** `lookup_station` returns `None`
  inside `driving_info`; the station is dropped during the radius-filter
  step. Safe degradation, no error.
- **Cache poisoning.** A first call with `max_drive_minutes=30` returns a
  smaller list than a second call with `max_drive_minutes=60`. Both are
  cached separately because the cache key is the (home, max_minutes)
  tuple. No staleness.
- **User changes home station between optimizations.** The cache key
  changes; a fresh Places call is made. Old entry remains in the dict but
  is harmless (Streamlit-process memory only).
- **The discovered station is the home station itself.** Filtered out
  during the drive-radius step (`drive_min == 0`, which we treat as
  invalid: home is not a "park station" in any meaningful sense). Add an
  explicit `name != home_station` guard.

## Error handling

New exception class `TransitClientError(Exception)` in `transit_client.py`
(if not already present — currently the module raises generic exceptions
from some functions). Single root for any failure during Places API or
driving_info that prevents `nearby_park_stations` from completing.

In `ui/optimization.py::_run_optimization`, the existing `except Exception`
block catches it. Detection: `isinstance(opt_exc, TransitClientError)`
selects a more specific title and body:

```python
if isinstance(opt_exc, TransitClientError):
    report_error(
        "Park-Bahnhof-Suche fehlgeschlagen",
        details=(
            f"Auto-Anfahrt wollte automatisch Park-Bahnhöfe im Umkreis "
            f"von {max_car_minutes} Min suchen, aber Google Maps Places "
            f"hat einen Fehler gemeldet.\n\n"
            f"Tipp: Auto-Anfahrt vorübergehend ausschalten (Schieber auf 0) "
            f"und nur die Tour-Bahnhöfe als Park-Optionen verwenden."
        ),
        exc=opt_exc,
    )
else:
    report_error(...)  # existing path
```

## File checklist

- `fahrtenplaner/transit_client.py`
  - Add `TransitClientError` exception class (if not already present).
  - Add `_park_stations_cache: dict[tuple[str, int], list[str]]`.
  - Add `_S_BAHN_NAME_PATTERN` and `_is_pure_sbahn_name`.
  - Add `nearby_park_stations(home_station, max_drive_minutes, top_k=15)`.
- `fahrtenplaner/optimizer.py`
  - Add constant `AUTO_PARK_STATIONS_TOP_K = 15`.
  - `optimize_day_car_mode`: new keyword-only `additional_park_stations:
    list[str] = ()` parameter; merge into `candidates` set.
  - `optimize_with_modes`: call `nearby_park_stations` when car-mode active;
    forward result to `optimize_day_car_mode`.
- `fahrtenplaner/ui/optimization.py`
  - `_run_optimization`: branch the `except` block to detect
    `TransitClientError` and produce a specific error message.
- `tests/test_transit_client.py`
  - Add `TestNearbyParkStations` (6 tests).
- `tests/test_optimizer.py`
  - Add `TestOptimizeWithModesAutoPark` (4 tests).

All code comments in English. UI strings stay in German.
