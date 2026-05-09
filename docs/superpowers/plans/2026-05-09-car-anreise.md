# Car-Anreise (Auto-Anfahrt) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the user drive their own car (≤ X minutes) to a near-by tour-departure station, do the tour chain there, and drive back from the same station — with fuel cost subtracted from gross revenue when comparing plans.

**Architecture:** Add a sibling car-mode optimizer next to the existing transit-only `optimize_day`. New `optimize_with_modes` orchestrator runs both and returns an `OptimizationResult(winner, alternative)`. UI shows the winner card with the alternative as a collapsed expander. Net euros (gross − fuel cost) is the comparison key.

**Tech Stack:** Python 3.12, Streamlit, Google Maps Directions API (driving mode added), pydeck (dashed segments for car drives). No new top-level dependencies.

**Spec:** `docs/superpowers/specs/2026-05-09-car-anreise-design.md`

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `fahrtenplaner/models.py` | Modify | New `CarLeg`, `OptimizationResult` dataclasses; extend `ChainLink` and `DayPlan` |
| `fahrtenplaner/transit_client.py` | Modify | New `driving_info(from_id, to_id) → Optional[(int, float)]` |
| `fahrtenplaner/optimizer.py` | Modify | New `optimize_day_car_mode()` and `optimize_with_modes()` orchestrator; existing `optimize_day` untouched |
| `fahrtenplaner/ui/state.py` | Modify | Add `fuel_consumption` (7.0) and `fuel_price` (1.79) session-state defaults |
| `fahrtenplaner/ui/sidebar.py` | Modify | New "Auto" expander with the two fuel inputs |
| `fahrtenplaner/ui/optimization.py` | Modify | New 4th-column input `Max. Auto-Anfahrt`; cap `max_gap_minutes` UI at 240; switch to `optimize_with_modes`; store `OptimizationResult` in `session_state.last_plan` |
| `fahrtenplaner/ui/render.py` | Modify | `render_result(result: OptimizationResult)` shows winner card with net-€ headline + breakdown; alternative expander; new `_render_car_leg_block` helper |
| `fahrtenplaner/ui/map.py` | Modify | Amber dashed segments for `car_outbound` / `car_inbound`; legend update |
| `fahrtenplaner/assets/style.css` | Modify | New `.auto-leg` block styling (amber accent) |
| `tests/test_optimizer.py` | Modify | 3 new tests (only the essential ones) |
| `tests/test_transit_client.py` | Modify | 1 new test (`driving_info`) |
| `CLAUDE.md` | Modify | Document the `OptimizationResult` migration |

**Test budget:** existing 34 → ~38. Strictly the 4 cases that catch real bug classes; everything else is implicitly covered.

---

## Task 1: Models — `CarLeg`, `OptimizationResult`, extended `ChainLink`/`DayPlan`

**Files:**
- Modify: `fahrtenplaner/models.py`

Pure data-model additions. No behavior change → no new tests in this task; existing 34 tests must keep passing as the regression check.

- [ ] **Step 1.1: Add `CarLeg` dataclass**

In `fahrtenplaner/models.py`, after the `Leg` dataclass (around line 50, where `Connection` follows `Leg`), insert:

```python
@dataclass
class CarLeg:
    """A single-segment car drive between two stations."""
    from_station: str
    to_station: str
    minutes: int        # one-way driving time
    km: float           # one-way distance
    cost: float = 0.0   # fuel cost for THIS leg only (computed by optimizer)
```

- [ ] **Step 1.2: Extend `ChainLink` with `car_leg` field and updated `label`**

In `fahrtenplaner/models.py`, the `ChainLink` dataclass currently looks like:

```python
@dataclass
class ChainLink:
    """Ein Element im Tagesplan: entweder Tour oder Transfer."""
    type: str  # "tour", "transfer", "outbound", "inbound"
    tour: Optional[Tour] = None
    connection: Optional[Connection] = None
    warning: Optional[str] = None

    @property
    def label(self) -> str:
        if self.type == "tour" and self.tour:
            return f"Tour {self.tour.tour_nr}"
        if self.type == "outbound":
            return "Anreise"
        if self.type == "inbound":
            return "Rückreise"
        return "Transfer"
```

Replace with:

```python
@dataclass
class ChainLink:
    """Ein Element im Tagesplan: entweder Tour, Transfer oder Auto-Drive."""
    type: str  # "tour" | "transfer" | "outbound" | "inbound" | "car_outbound" | "car_inbound"
    tour: Optional[Tour] = None
    connection: Optional[Connection] = None
    car_leg: Optional[CarLeg] = None
    warning: Optional[str] = None

    @property
    def label(self) -> str:
        labels = {
            "tour":         f"Tour {self.tour.tour_nr}" if self.tour else "Tour",
            "outbound":     "Anreise",
            "inbound":      "Rückreise",
            "car_outbound": "Auto-Anfahrt",
            "car_inbound":  "Auto-Rückfahrt",
        }
        return labels.get(self.type, "Transfer")
```

- [ ] **Step 1.3: Extend `DayPlan` with cost properties**

In `fahrtenplaner/models.py`, the `DayPlan` dataclass currently has `tours`, `total_euros`, `num_tours`, `warnings`, `time_range` properties. Add three new properties after `total_euros`:

```python
    @property
    def total_costs(self) -> float:
        """Sum of fuel costs across all car legs in the chain."""
        return sum(link.car_leg.cost for link in self.chain
                   if link.car_leg is not None)

    @property
    def net_euros(self) -> float:
        """Gross revenue minus fuel cost — the comparison key for winner selection."""
        return self.total_euros - self.total_costs

    @property
    def has_car_legs(self) -> bool:
        """True iff this plan uses car-mode (any car leg in the chain)."""
        return any(link.car_leg is not None for link in self.chain)
```

- [ ] **Step 1.4: Add `OptimizationResult` dataclass at end of file**

Append to `fahrtenplaner/models.py`:

```python
@dataclass
class OptimizationResult:
    """A primary plan + an optional alternative for side-by-side comparison."""
    winner: DayPlan
    alternative: Optional["DayPlan"] = None

    @property
    def has_alternative(self) -> bool:
        return self.alternative is not None and self.alternative.num_tours > 0
```

- [ ] **Step 1.5: Run all existing tests to verify nothing broke**

Run:
```bash
uv run --no-project --python 3.12 --with-requirements requirements.txt --with pytest pytest tests/ -q
```
Expected: `34 passed`.

- [ ] **Step 1.6: Commit**

```bash
git add fahrtenplaner/models.py
git commit -m "models: add CarLeg, OptimizationResult, extend ChainLink and DayPlan"
```

---

## Task 2: Transit client — `driving_info`

**Files:**
- Modify: `fahrtenplaner/transit_client.py`
- Modify: `tests/test_transit_client.py`

- [ ] **Step 2.1: Write the failing test**

In `tests/test_transit_client.py`, append this test class at the end:

```python
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
```

- [ ] **Step 2.2: Run test to verify it fails**

Run:
```bash
uv run --no-project --python 3.12 --with-requirements requirements.txt --with pytest pytest tests/test_transit_client.py::TestDrivingInfo::test_returns_minutes_and_km -v
```
Expected: FAIL with `ImportError: cannot import name 'driving_info'`.

- [ ] **Step 2.3: Add `driving_info` to `transit_client.py`**

In `fahrtenplaner/transit_client.py`, find the `_connection_cache` declaration (near the existing caches) and add a new cache:

```python
_driving_cache: dict[str, Optional[tuple[int, float]]] = {}
```

Then, after the `find_connection` function (around line 130–150), insert:

```python
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
```

- [ ] **Step 2.4: Run test to verify it passes**

Run:
```bash
uv run --no-project --python 3.12 --with-requirements requirements.txt --with pytest pytest tests/test_transit_client.py::TestDrivingInfo::test_returns_minutes_and_km -v
```
Expected: PASS.

- [ ] **Step 2.5: Run full suite**

Run:
```bash
uv run --no-project --python 3.12 --with-requirements requirements.txt --with pytest pytest tests/ -q
```
Expected: `35 passed`.

- [ ] **Step 2.6: Commit**

```bash
git add fahrtenplaner/transit_client.py tests/test_transit_client.py
git commit -m "transit_client: add driving_info() for car-mode distance/time lookups"
```

---

## Task 3: Optimizer — `optimize_day_car_mode`

**Files:**
- Modify: `fahrtenplaner/optimizer.py`
- Modify: `tests/test_optimizer.py`

The new function is a sibling to `optimize_day` (transit-only) but constrains chain start = chain end = candidate car-park station. It iterates over candidate stations and picks the highest-net-euros chain across all candidates.

- [ ] **Step 3.1: Write the failing test**

In `tests/test_optimizer.py`, append at end:

```python
class TestCarMode:
    """Car-mode optimizer finds chains unreachable by transit alone."""

    def test_finds_chain_starting_and_ending_at_same_station(self):
        """A 5:00 tour at Pasewalk is reachable when home (Prenzlau) is 30 min by car."""
        from optimizer import optimize_day_car_mode
        from transit_client import lookup_station, driving_info

        # Two tours, both starting AND ending at Pasewalk on the same day.
        tour_a = make_tour(704347, "05:30", "Pasewalk", "08:00", "Pasewalk", 50.0)
        tour_b = make_tour(704348, "10:00", "Pasewalk", "14:00", "Pasewalk", 60.0)

        # Mocks: home & Pasewalk geocode; driving Prenzlau→Pasewalk = 30 min, 32 km;
        # transfer A→B is feasible at same station.
        with patch("optimizer.batch_lookup_stations", return_value={
            "Prenzlau": {"id": "ChIJ_prenzlau", "name": "Prenzlau"},
            "Pasewalk": {"id": "ChIJ_pasewalk", "name": "Pasewalk"},
        }), patch("optimizer.check_reachability_with_ids", return_value=Connection(legs=[])), \
             patch("optimizer.driving_info", return_value=(30, 32.0)):

            plan = optimize_day_car_mode(
                tours=[tour_a, tour_b],
                home_station="Prenzlau",
                earliest_departure=datetime.combine(DAY, time(4, 0)),
                latest_return=datetime.combine(DAY, time(23, 59)),
                max_car_minutes=60,
                fuel_consumption=7.0,
                fuel_price=1.79,
            )

        # Both tours should be in the chain (same station, no time conflict).
        assert plan.num_tours == 2
        # Chain starts and ends at Pasewalk via car legs.
        assert plan.chain[0].type == "car_outbound"
        assert plan.chain[-1].type == "car_inbound"
        assert plan.chain[0].car_leg.to_station == "Pasewalk"
        assert plan.chain[-1].car_leg.to_station == "Prenzlau"
        # Cost = 2 × 32 km × (7/100 × 1.79) = 7.99 €
        assert plan.total_costs == pytest.approx(7.9968, abs=0.001)
        # Net euros = 110 − 8 ≈ 102
        assert plan.net_euros == pytest.approx(102.0, abs=0.1)
```

Add the necessary imports at the top of the test file if not already present:
```python
from unittest.mock import patch
from models import Connection
import pytest
```

- [ ] **Step 3.2: Run test to verify it fails**

Run:
```bash
uv run --no-project --python 3.12 --with-requirements requirements.txt --with pytest pytest tests/test_optimizer.py::TestCarMode::test_finds_chain_starting_and_ending_at_same_station -v
```
Expected: FAIL with `ImportError: cannot import name 'optimize_day_car_mode'`.

- [ ] **Step 3.3: Add `optimize_day_car_mode` to `optimizer.py`**

At the top of `fahrtenplaner/optimizer.py`, ensure the imports include the new model and the new transit_client function:

```python
from models import CarLeg, ChainLink, Connection, DayPlan, OptimizationResult, Tour
from transit_client import (
    batch_lookup_stations,
    check_reachability_with_ids,
    driving_info,
    stations_match,
)
```

After the existing `optimize_day` function (and before `_check_transfer_warning`), insert:

```python
def optimize_day_car_mode(
    tours: list[Tour],
    home_station: str,
    earliest_departure: datetime,
    latest_return: datetime,
    max_car_minutes: int,
    fuel_consumption: float,
    fuel_price: float,
    progress_callback: ProgressCb = None,
    max_transfer_gap_hours: float = MAX_TRANSFER_GAP_HOURS,
) -> DayPlan:
    """Find the best chain that starts AND ends at a tour-departure station
    within `max_car_minutes` driving radius from `home_station`. The user drives
    to that station, does the tour chain, then drives back from there.
    """
    if not tours or max_car_minutes <= 0:
        return DayPlan()

    report = _normalize_progress(progress_callback)

    # Resolve all stations once (shared with potential transit pass via cache).
    station_names = _collect_station_names(tours, home_station, home_station)
    station_ids = batch_lookup_stations(list(station_names))
    get_id = _id_lookup(station_ids)
    home_id = get_id(home_station)
    if not home_id:
        return DayPlan()

    # Build the transit transfer matrix once (shared with sister transit-mode call).
    edge, _ = _compute_transfer_matrix(
        sorted(tours, key=lambda t: t.departure_dt),
        get_id, max_transfer_gap_hours, report,
    )

    sorted_tours = sorted(tours, key=lambda t: t.departure_dt)
    candidates = sorted({t.departure_station for t in sorted_tours})

    cost_per_km = (fuel_consumption / 100.0) * fuel_price
    best_plan = DayPlan()

    for candidate in candidates:
        cand_id = get_id(candidate)
        if not cand_id:
            continue
        info = driving_info(home_id, cand_id)
        if info is None:
            continue
        drive_min, drive_km = info
        if drive_min > max_car_minutes:
            continue

        plan = _build_car_chain_for_candidate(
            sorted_tours, edge, candidate, drive_min, drive_km, cost_per_km,
            earliest_departure, latest_return, home_station,
        )
        if plan.net_euros > best_plan.net_euros:
            best_plan = plan

    return best_plan


def _build_car_chain_for_candidate(
    tours: list[Tour],
    edge: list[list[Optional[Connection]]],
    candidate: str,
    drive_min: int,
    drive_km: float,
    cost_per_km: float,
    earliest_departure: datetime,
    latest_return: datetime,
    home_station: str,
) -> DayPlan:
    """Run the constrained DAG-DP for one candidate car-park station and
    materialize the resulting chain into a DayPlan with car legs.
    """
    n = len(tours)
    car_arrival = earliest_departure + timedelta(minutes=drive_min)
    latest_tour_arrival = latest_return - timedelta(minutes=drive_min)

    dp: list[float] = [NEG_INF] * n
    pred: list[int] = [-1] * n

    # Seed: tours that start at the candidate station after car arrival.
    for i, tour in enumerate(tours):
        if tour.departure_station == candidate and tour.departure_dt >= car_arrival:
            dp[i] = tour.euros

    # Standard DAG-DP transition (uses shared transfer matrix).
    for j in range(n):
        for i in range(j):
            if edge[i][j] is None or dp[i] == NEG_INF:
                continue
            new_val = dp[i] + tours[j].euros
            if new_val > dp[j]:
                dp[j] = new_val
                pred[j] = i

    # Best end: tour ending at the candidate station, with time to drive back.
    best_val = NEG_INF
    best_j = -1
    for j, val in enumerate(dp):
        if val == NEG_INF:
            continue
        if tours[j].arrival_station != candidate:
            continue
        if tours[j].arrival_dt > latest_tour_arrival:
            continue
        if val > best_val:
            best_val = val
            best_j = j

    if best_j == -1:
        return DayPlan()

    chain_indices = _reconstruct_chain(pred, best_j)
    leg_cost = drive_km * cost_per_km

    plan = DayPlan()
    plan.chain.append(ChainLink(
        type="car_outbound",
        car_leg=CarLeg(
            from_station=home_station, to_station=candidate,
            minutes=drive_min, km=drive_km, cost=leg_cost,
        ),
    ))
    for pos, idx in enumerate(chain_indices):
        plan.chain.append(ChainLink(type="tour", tour=tours[idx]))
        if pos < len(chain_indices) - 1:
            next_idx = chain_indices[pos + 1]
            plan.chain.append(_transfer_link(
                tours[idx], tours[next_idx], edge[idx][next_idx],
            ))
    plan.chain.append(ChainLink(
        type="car_inbound",
        car_leg=CarLeg(
            from_station=candidate, to_station=home_station,
            minutes=drive_min, km=drive_km, cost=leg_cost,
        ),
    ))
    return plan
```

- [ ] **Step 3.4: Run the new test to verify it passes**

Run:
```bash
uv run --no-project --python 3.12 --with-requirements requirements.txt --with pytest pytest tests/test_optimizer.py::TestCarMode::test_finds_chain_starting_and_ending_at_same_station -v
```
Expected: PASS.

- [ ] **Step 3.5: Run full suite**

Run:
```bash
uv run --no-project --python 3.12 --with-requirements requirements.txt --with pytest pytest tests/ -q
```
Expected: `36 passed`.

- [ ] **Step 3.6: Commit**

```bash
git add fahrtenplaner/optimizer.py tests/test_optimizer.py
git commit -m "optimizer: add optimize_day_car_mode for same-station car chains"
```

---

## Task 4: Optimizer — `optimize_with_modes` orchestrator

**Files:**
- Modify: `fahrtenplaner/optimizer.py`
- Modify: `tests/test_optimizer.py`

Combines transit-only and car-mode evaluation. Returns `OptimizationResult(winner, alternative)`. Two essential tests embedded.

- [ ] **Step 4.1: Write the failing tests**

In `tests/test_optimizer.py`, append to the `TestCarMode` class:

```python
    def test_winner_is_decided_by_net_euros(self):
        """Transit gross 200 vs Car gross 215 with 20 € fuel → transit wins (net 200 > 195)."""
        from optimizer import optimize_with_modes
        from models import DayPlan, ChainLink, Tour, CarLeg

        # Build two pre-cooked plans to inject. Real optimizer integration
        # is covered by other tests; here we test the *comparison logic*.
        transit_plan = DayPlan()
        # 200 € gross via a single tour
        transit_plan.chain.append(ChainLink(type="tour", tour=make_tour(
            1, "08:00", "Prenzlau", "10:00", "Prenzlau", 200.0,
        )))

        car_plan = DayPlan()
        car_plan.chain.append(ChainLink(type="car_outbound", car_leg=CarLeg(
            from_station="Prenzlau", to_station="Pasewalk",
            minutes=30, km=50, cost=10.0,
        )))
        car_plan.chain.append(ChainLink(type="tour", tour=make_tour(
            2, "08:00", "Pasewalk", "10:00", "Pasewalk", 215.0,
        )))
        car_plan.chain.append(ChainLink(type="car_inbound", car_leg=CarLeg(
            from_station="Pasewalk", to_station="Prenzlau",
            minutes=30, km=50, cost=10.0,
        )))

        # Net: transit = 200, car = 215 - 20 = 195
        with patch("optimizer.optimize_day", return_value=transit_plan), \
             patch("optimizer.optimize_day_car_mode", return_value=car_plan):
            result = optimize_with_modes(
                tours=[transit_plan.chain[0].tour, car_plan.chain[1].tour],
                home_station="Prenzlau", dest_station="Prenzlau",
                earliest_departure=datetime.combine(DAY, time(4, 0)),
                latest_return=datetime.combine(DAY, time(23, 59)),
                max_car_minutes=30,
                fuel_consumption=7.0,
                fuel_price=1.79,
            )

        assert result.winner.net_euros == pytest.approx(200.0, abs=0.1)
        assert result.alternative is not None
        assert result.alternative.net_euros == pytest.approx(195.0, abs=0.1)

    def test_car_mode_skipped_when_dest_differs(self):
        """When dest_station != home_station, the car-mode pass is skipped."""
        from optimizer import optimize_with_modes
        from models import DayPlan

        with patch("optimizer.optimize_day", return_value=DayPlan()) as transit_mock, \
             patch("optimizer.optimize_day_car_mode") as car_mock:
            optimize_with_modes(
                tours=[],
                home_station="Prenzlau", dest_station="Stralsund",  # ≠ home
                earliest_departure=datetime.combine(DAY, time(4, 0)),
                latest_return=datetime.combine(DAY, time(23, 59)),
                max_car_minutes=30,
                fuel_consumption=7.0,
                fuel_price=1.79,
            )

        transit_mock.assert_called_once()
        car_mock.assert_not_called()  # never reaches the car-mode pass
```

- [ ] **Step 4.2: Run tests to verify they fail**

Run:
```bash
uv run --no-project --python 3.12 --with-requirements requirements.txt --with pytest pytest tests/test_optimizer.py::TestCarMode -v
```
Expected: 2 new tests FAIL with `ImportError: cannot import name 'optimize_with_modes'`.

- [ ] **Step 4.3: Add `optimize_with_modes` to `optimizer.py`**

After `optimize_day_car_mode`, insert:

```python
def optimize_with_modes(
    tours: list[Tour],
    home_station: str,
    dest_station: str,
    earliest_departure: datetime,
    latest_return: datetime,
    max_car_minutes: int,
    fuel_consumption: float,
    fuel_price: float,
    progress_callback: ProgressCb = None,
    max_transfer_gap_hours: float = MAX_TRANSFER_GAP_HOURS,
) -> OptimizationResult:
    """Run transit-mode and (optionally) car-mode optimization, return both as
    winner + alternative. Net euros (gross − fuel cost) decides the winner.
    Tie → transit wins (no car needed).
    """
    transit_plan = optimize_day(
        tours, home_station, dest_station,
        earliest_departure, latest_return,
        progress_callback=progress_callback,
        max_transfer_gap_hours=max_transfer_gap_hours,
    )

    car_plan: DayPlan = DayPlan()
    if max_car_minutes > 0 and stations_match(home_station, dest_station):
        car_plan = optimize_day_car_mode(
            tours, home_station,
            earliest_departure, latest_return,
            max_car_minutes, fuel_consumption, fuel_price,
            progress_callback=progress_callback,
            max_transfer_gap_hours=max_transfer_gap_hours,
        )

    candidates = [p for p in (transit_plan, car_plan) if p.num_tours > 0]
    if not candidates:
        return OptimizationResult(winner=DayPlan(), alternative=None)

    # Sort by net euros desc, tie-break: transit wins (no car legs).
    candidates.sort(key=lambda p: (-p.net_euros, p.has_car_legs))
    winner = candidates[0]
    alternative = candidates[1] if len(candidates) > 1 else None
    return OptimizationResult(winner=winner, alternative=alternative)
```

- [ ] **Step 4.4: Run tests to verify they pass**

Run:
```bash
uv run --no-project --python 3.12 --with-requirements requirements.txt --with pytest pytest tests/test_optimizer.py::TestCarMode -v
```
Expected: 3 tests in `TestCarMode` PASS.

- [ ] **Step 4.5: Run full suite**

Run:
```bash
uv run --no-project --python 3.12 --with-requirements requirements.txt --with pytest pytest tests/ -q
```
Expected: `38 passed`.

- [ ] **Step 4.6: Commit**

```bash
git add fahrtenplaner/optimizer.py tests/test_optimizer.py
git commit -m "optimizer: add optimize_with_modes orchestrator (transit + car comparison)"
```

---

## Task 5: Session-state defaults for fuel inputs

**Files:**
- Modify: `fahrtenplaner/ui/state.py`

- [ ] **Step 5.1: Add fuel-input defaults**

In `fahrtenplaner/ui/state.py`, inside `init_session_state()`, after the `last_plan_log` block, add:

```python
    if "fuel_consumption" not in st.session_state:
        st.session_state.fuel_consumption = 7.0
    if "fuel_price" not in st.session_state:
        st.session_state.fuel_price = 1.79
```

- [ ] **Step 5.2: Commit**

```bash
git add fahrtenplaner/ui/state.py
git commit -m "ui/state: default fuel consumption (7 l/100km) and price (1.79 €/l)"
```

---

## Task 6: Sidebar — fuel input expander

**Files:**
- Modify: `fahrtenplaner/ui/sidebar.py`

- [ ] **Step 6.1: Add `_render_fuel_section` helper**

In `fahrtenplaner/ui/sidebar.py`, after `_render_stations` (near line 80, where existing `_render_*` helpers live), insert:

```python
def _render_fuel_section() -> None:
    """Sidebar 'Auto' expander: fuel consumption and current fuel price."""
    with st.sidebar.expander("Auto"):
        st.session_state.fuel_consumption = st.number_input(
            "Auto-Verbrauch (l/100 km)",
            min_value=0.0, max_value=30.0, step=0.1,
            value=float(st.session_state.fuel_consumption),
            help="Verbrauch eures Autos. Steht meist im Bordcomputer oder Fahrzeugschein.",
        )
        st.session_state.fuel_price = st.number_input(
            "Spritpreis (€/l)",
            min_value=0.0, max_value=5.0, step=0.01,
            value=float(st.session_state.fuel_price),
            help="Aktueller Spritpreis an der Tankstelle.",
        )
```

- [ ] **Step 6.2: Wire it into `render_sidebar`**

In `fahrtenplaner/ui/sidebar.py`, find `render_sidebar()` (the public entry function near the bottom of the file). After the line that calls `_render_stations()` (and before the `Touren laden` button), add:

```python
    _render_fuel_section()
```

- [ ] **Step 6.3: Smoke-test the launcher boots**

Run:
```bash
uv run --no-project --python 3.12 --with-requirements requirements.txt python -c "
import subprocess, sys, time, urllib.request
PORT = 8561
proc = subprocess.Popen(
    [sys.executable, 'launcher.py', '--streamlit-server', '--port', str(PORT)],
    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
)
try:
    ok = False
    for _ in range(60):
        try:
            urllib.request.urlopen(f'http://127.0.0.1:{PORT}', timeout=1)
            ok = True; break
        except Exception:
            time.sleep(0.5)
    print('boots:', ok)
finally:
    proc.terminate()
    try: proc.wait(timeout=5)
    except Exception: proc.kill()
"
```
Expected: `boots: True`.

- [ ] **Step 6.4: Commit**

```bash
git add fahrtenplaner/ui/sidebar.py
git commit -m "ui/sidebar: add Auto expander with fuel consumption and fuel price"
```

---

## Task 7: Optimization panel — `Max. Auto-Anfahrt` input + cap + `OptimizationResult` migration

**Files:**
- Modify: `fahrtenplaner/ui/optimization.py`

This task changes 3 things at once because they're tightly coupled: (1) new car-input field, (2) `max_gap_minutes` UI cap, (3) `optimize_day` → `optimize_with_modes` migration.

- [ ] **Step 7.1: Cap `max_gap_minutes` at 240**

In `fahrtenplaner/ui/optimization.py`, find the `_render_param_inputs()` function. The current `max_gap_minutes` widget reads:

```python
max_gap_minutes = st.number_input(
    "Max. Pause zwischen Touren (Min.)",
    min_value=10, max_value=1440, value=60, step=10,
    help="Maximale Zeit zwischen Ende einer Tour und Beginn der nächsten (inkl. Leerfahrt)",
)
```

Replace `max_value=1440` with `max_value=240`.

- [ ] **Step 7.2: Add `Max. Auto-Anfahrt` as a 4th column**

Still in `_render_param_inputs()`, change the column layout from `col1, col2, col3 = st.columns(3)` to `col1, col2, col3, col4 = st.columns(4)`. Then add a new `with col4:` block at the end of the function (and update the return signature):

```python
def _render_param_inputs(same_station: bool) -> tuple[time, time, int, int]:
    col1, col2, col3, col4 = st.columns(4)
    # ... existing col1, col2, col3 unchanged ...
    with col4:
        max_car_minutes = st.number_input(
            "Max. Auto-Anfahrt (Min.)",
            min_value=0, max_value=120, step=5,
            value=0,
            disabled=not same_station,
            help=(
                "Wie weit dürft ihr morgens mit dem Auto fahren, um den Startbahnhof "
                "zu erreichen? 0 = kein Auto."
                if same_station else
                "Auto-Modus erfordert Ankunft = Abfahrt."
            ),
        )
    return dep_time, ret_time, int(max_gap_minutes), int(max_car_minutes)
```

- [ ] **Step 7.3: Update `_render_param_inputs` caller and `_run_optimization`**

In the `render_optimization_section()` function (the public entry near the bottom of the file), find where `_render_param_inputs()` is called and update:

```python
dep_time, ret_time, max_gap_minutes, max_car_minutes = _render_param_inputs(ctx.same_station)
```

Then locate `_run_optimization()` (the helper that builds `optimize_day(...)`) and replace its body to call `optimize_with_modes`:

```python
def _run_optimization(
    day_tours: list[Tour], ctx: SidebarContext,
    dep_time: time, ret_time: time, max_gap_minutes: int, max_car_minutes: int,
) -> None:
    earliest = datetime.combine(ctx.selected_date, dep_time)
    latest = datetime.combine(ctx.selected_date, ret_time)
    if latest <= earliest:
        latest += timedelta(days=1)

    progress_bar = st.progress(0, text="Starte Optimierung...")
    log_messages: list[str] = []

    def progress_cb(pct: float, msg: str) -> None:
        progress_bar.progress(min(pct, 1.0), text=msg)
        log_messages.append(msg)

    opt_exc: Exception | None = None
    try:
        result = optimize_with_modes(
            tours=day_tours,
            home_station=ctx.home_station,
            dest_station=ctx.dest_station,
            earliest_departure=earliest,
            latest_return=latest,
            max_car_minutes=max_car_minutes,
            fuel_consumption=st.session_state.fuel_consumption,
            fuel_price=st.session_state.fuel_price,
            progress_callback=progress_cb,
            max_transfer_gap_hours=max_gap_minutes / 60,
        )
    except Exception as e:
        result = OptimizationResult(winner=DayPlan(), alternative=None)
        opt_exc = e

    progress_bar.empty()
    st.session_state.last_plan = result
    st.session_state.last_plan_log = log_messages

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

Also update the call to `_run_optimization` in the orchestrator and the call site that reads `st.session_state.last_plan` (in `render_optimization_section`):

```python
    if st.button(
        "Optimale Route berechnen",
        type="primary",
        use_container_width=True,
        disabled=not day_tours or not ctx.home_station,
    ):
        _run_optimization(day_tours, ctx, dep_time, ret_time, max_gap_minutes, max_car_minutes)

    result = st.session_state.last_plan  # now Optional[OptimizationResult]
    if result is None:
        _render_empty_state(day_tours, ctx)
    elif result.winner.num_tours == 0:
        _render_no_chain_warning()
        _render_optimization_log()
    else:
        render_result(result)
        _render_optimization_log()
```

- [ ] **Step 7.4: Update imports at top of file**

In `fahrtenplaner/ui/optimization.py` change the `from models import` line to include `OptimizationResult`:

```python
from models import DayPlan, OptimizationResult, Tour
from optimizer import optimize_with_modes
```

(remove `from optimizer import optimize_day` if still present).

- [ ] **Step 7.5: Run tests**

Run:
```bash
uv run --no-project --python 3.12 --with-requirements requirements.txt --with pytest pytest tests/ -q
```
Expected: `38 passed` (UI changes don't affect test count).

- [ ] **Step 7.6: Smoke-test launcher**

Run the same launcher smoke test as Task 6 step 3 (PORT=8562). Expected: `boots: True`.

- [ ] **Step 7.7: Commit**

```bash
git add fahrtenplaner/ui/optimization.py
git commit -m "ui/optimization: add Max. Auto-Anfahrt input, switch to optimize_with_modes"
```

---

## Task 8: Render — winner card with net €, alternative expander, car-leg block

**Files:**
- Modify: `fahrtenplaner/ui/render.py`

- [ ] **Step 8.1: Add `_render_car_leg_block` helper**

In `fahrtenplaner/ui/render.py`, after `_render_connection_block`, insert:

```python
def _render_car_leg_block(link: ChainLink) -> None:
    """Single-line block for car-mode anreise/rückreise drives."""
    leg = link.car_leg
    if not leg:
        return
    icon = "🚗"
    label = link.label  # "Auto-Anfahrt" or "Auto-Rückfahrt"
    cost_str = f"{leg.cost:.2f} €".replace(".", ",")
    st.markdown(
        f"""
        <div class="auto-leg">
          <span class="icon">{icon}</span>
          <span class="label">{label}</span>
          <span class="meta">{leg.minutes} min · {leg.km:.0f} km · {cost_str} Sprit</span>
          <span class="dest">→ {leg.to_station}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )
```

- [ ] **Step 8.2: Add `_render_winner_metrics` helper**

In `fahrtenplaner/ui/render.py`, before `render_result`, insert:

```python
def _render_winner_metrics(plan: DayPlan, fuel_consumption: float, fuel_price: float) -> None:
    """Three-line metric stack with optional cost breakdown for car-mode plans."""
    if plan.has_car_legs:
        st.success(f"Optimaler Auto-Plan · {plan.net_euros:.2f} € netto".replace(".", ","))
    else:
        st.success(f"Optimaler Transit-Plan · {plan.total_euros:.2f} €".replace(".", ","))

    m1, m2, m3 = st.columns(3)
    m1.metric(
        "Gesamtverdienst",
        f"{plan.net_euros:.2f} € netto".replace(".", ","),
        delta=f"−{plan.total_costs:.2f} € Sprit".replace(".", ",") if plan.has_car_legs else None,
    )
    m2.metric("Anzahl Touren", plan.num_tours)
    m3.metric("Zeitraum", plan.time_range)

    if plan.has_car_legs:
        total_km = sum(link.car_leg.km * 2 for link in plan.chain
                       if link.car_leg is not None) / 2  # one-way km × 2 = round-trip
        st.caption(
            f"Verdienst {plan.total_euros:.2f} € − Sprit {plan.total_costs:.2f} € "
            f"({total_km:.0f} km · {fuel_consumption:.1f} l/100km · {fuel_price:.2f} €/l) "
            f"= {plan.net_euros:.2f} € netto".replace(".", ",")
        )
```

- [ ] **Step 8.3: Refactor `render_result` to accept `OptimizationResult`**

In `fahrtenplaner/ui/render.py`, replace the existing `render_result(plan: DayPlan)` function with:

```python
def render_result(result: OptimizationResult) -> None:
    """Render winner card + optional alternative expander."""
    fuel_consumption = float(st.session_state.get("fuel_consumption", 7.0))
    fuel_price = float(st.session_state.get("fuel_price", 1.79))

    _render_plan_full(result.winner, fuel_consumption, fuel_price, map_key="winner")

    if not result.has_alternative:
        return

    alt = result.alternative
    label = (
        f"▸ Alternative anzeigen · "
        f"{'Auto' if alt.has_car_legs else 'Transit'} "
        f"{alt.net_euros:.2f} € netto ({alt.num_tours} Touren)"
    ).replace(".", ",")
    with st.expander(label, expanded=False):
        _render_plan_full(alt, fuel_consumption, fuel_price, map_key="alternative")


def _render_plan_full(
    plan: DayPlan, fuel_consumption: float, fuel_price: float, map_key: str = "winner",
) -> None:
    """Full plan rendering: metrics + warnings + map toggle + chain + summary."""
    _render_winner_metrics(plan, fuel_consumption, fuel_price)

    for warning in plan.warnings:
        st.warning(warning)

    st.divider()

    if st.toggle(
        "Tagesroute auf Karte anzeigen",
        value=False,
        key=f"show_route_map_{map_key}",   # stable across reruns; unique per plan slot
        help="Zeigt Anreise (grau), Touren (rot) und Rückreise (grau) auf einer Karte.",
    ):
        render_route_map(plan)

    st.divider()

    st.subheader("Tagesplan")
    for link in plan.chain:
        if link.type == "outbound":
            _render_connection_block("🚉 Anreise", link)
        elif link.type == "inbound":
            _render_connection_block("🏠 Rückreise", link)
        elif link.type == "transfer":
            _render_connection_block("🔄 Transfer", link)
        elif link.type == "tour":
            _render_tour_block(link)
        elif link.type in ("car_outbound", "car_inbound"):
            _render_car_leg_block(link)

    st.divider()
    st.subheader("Zusammenfassung")
    _render_summary_table(plan)
```

- [ ] **Step 8.4: Update imports at top of `render.py`**

Ensure these are imported:

```python
from models import ChainLink, DayPlan, OptimizationResult
```

- [ ] **Step 8.5: Run tests**

Run:
```bash
uv run --no-project --python 3.12 --with-requirements requirements.txt --with pytest pytest tests/ -q
```
Expected: `38 passed`.

- [ ] **Step 8.6: Smoke-test launcher**

Same launcher smoke test (PORT=8563). Expected: `boots: True`.

- [ ] **Step 8.7: Commit**

```bash
git add fahrtenplaner/ui/render.py
git commit -m "ui/render: winner+alternative layout with net-€ headline and car-leg blocks"
```

---

## Task 9: Map — amber dashed segments for car drives

**Files:**
- Modify: `fahrtenplaner/ui/map.py`

- [ ] **Step 9.1: Add car-mode colors to `_MAP_COLORS`**

In `fahrtenplaner/ui/map.py`, the `_MAP_COLORS` dict currently has 4 entries. Add two more:

```python
_MAP_COLORS = {
    "outbound":     [108, 122, 140, 210],
    "tour":         [236,   0,  22, 235],
    "transfer":     [170, 170, 165, 190],
    "inbound":      [108, 122, 140, 210],
    "car_outbound": [212, 160,  23, 230],   # amber
    "car_inbound":  [212, 160,  23, 230],   # amber
}
```

- [ ] **Step 9.2: Update `_build_route_segments` to handle car legs**

In `fahrtenplaner/ui/map.py`, the existing `_build_route_segments` function iterates over `plan.chain` and uses `link.connection.legs`. For car legs, there's no `Connection` — the geometry comes from `link.car_leg.from_station` and `link.car_leg.to_station`. Replace the relevant branch in the function:

```python
        elif link.type in ("car_outbound", "car_inbound"):
            if link.car_leg is None:
                continue
            push(
                link.car_leg.from_station,
                link.car_leg.to_station,
                link.type,
                f"{link.label} · {link.car_leg.minutes} min, {link.car_leg.km:.0f} km",
            )
```

Also: when building each segment dict, include a `"dashed"` flag so the LineLayer knows which to render dashed. Modify the `push` inner function:

```python
        segments.append({
            "from": [a[0], a[1]],
            "to":   [b[0], b[1]],
            "color": _MAP_COLORS.get(ctype, [120, 120, 120, 200]),
            "label": label,
            "dashed": ctype in ("car_outbound", "car_inbound"),
        })
```

- [ ] **Step 9.3: Update the LineLayer call to use dashed style**

In `render_route_map`, where the existing `LineLayer` is built, split it into two layers — solid (for transit) and dashed (for car):

```python
    solid_segments = [s for s in segments if not s.get("dashed")]
    dashed_segments = [s for s in segments if s.get("dashed")]

    layers = []
    if solid_segments:
        layers.append(pdk.Layer(
            "LineLayer", data=solid_segments,
            get_source_position="from", get_target_position="to",
            get_color="color", get_width=5, pickable=True,
        ))
    if dashed_segments:
        layers.append(pdk.Layer(
            "LineLayer", data=dashed_segments,
            get_source_position="from", get_target_position="to",
            get_color="color", get_width=5,
            get_dash_array=[6, 4],
            extensions=[pdk.types.String("PathStyleExtension")] if hasattr(pdk.types, "String") else [],
            pickable=True,
        ))
    layers.append(station_layer)
```

(deck.gl's `PathStyleExtension` is what enables `getDashArray`. If pydeck's version doesn't expose it cleanly, the fallback is solid amber lines, which is still readable.)

- [ ] **Step 9.4: Update legend in `_render_map_legend`**

Replace the legend block to include the new "Auto" row:

```python
def _render_map_legend() -> None:
    def bar(color: str, dashed: bool = False) -> str:
        if dashed:
            return (
                f'<svg width="22" height="4" style="vertical-align:middle;margin-right:6px;" '
                f'aria-hidden="true"><line x1="0" y1="2" x2="22" y2="2" stroke="{color}" '
                f'stroke-width="3" stroke-dasharray="4 3"/></svg>'
            )
        return (
            f'<svg width="22" height="4" style="vertical-align:middle;margin-right:6px;" '
            f'aria-hidden="true"><rect width="22" height="4" rx="2" fill="{color}"/></svg>'
        )

    st.markdown(
        f"""
        <div class="map-legend">
          <span class="leg">{bar('#EC0016')}Tour (bezahlt)</span>
          <span class="leg">{bar('#6C7A8C')}Anreise / Rückreise</span>
          <span class="leg">{bar('#AAAAA5')}Transfer</span>
          <span class="leg">{bar('#D4A017', dashed=True)}Auto</span>
        </div>
        """,
        unsafe_allow_html=True,
    )
```

- [ ] **Step 9.5: Run tests + smoke-test launcher**

Run:
```bash
uv run --no-project --python 3.12 --with-requirements requirements.txt --with pytest pytest tests/ -q
```
Expected: `38 passed`.

Then run the launcher smoke test (PORT=8564). Expected: `boots: True`.

- [ ] **Step 9.6: Commit**

```bash
git add fahrtenplaner/ui/map.py
git commit -m "ui/map: amber dashed segments for car-mode drives + legend update"
```

---

## Task 10: CSS — `.auto-leg` styling

**Files:**
- Modify: `fahrtenplaner/assets/style.css`

- [ ] **Step 10.1: Append `.auto-leg` rules**

In `fahrtenplaner/assets/style.css`, append to the end of the file:

```css
/* --- Car-mode drive blocks (auto-anreise / auto-rückreise) ---------------- */
.auto-leg {
    display: flex;
    align-items: center;
    gap: 0.7rem;
    padding: 0.6rem 0.9rem;
    background: var(--surface);
    border: 1px solid #E8DAB6;
    border-left: 4px solid #D4A017;
    border-radius: var(--radius-sm);
    margin: 0.5rem 0;
    font-family: var(--sans);
}
.auto-leg .icon { font-size: 1.05rem; }
.auto-leg .label { font-weight: 600; color: var(--ink); font-size: 0.92rem; }
.auto-leg .meta {
    font-family: var(--mono);
    font-variant-numeric: tabular-nums;
    color: var(--sub);
    font-size: 0.85rem;
}
.auto-leg .dest { margin-left: auto; font-weight: 500; color: var(--ink); }
```

- [ ] **Step 10.2: Smoke-test launcher**

Run launcher smoke test (PORT=8565). Expected: `boots: True`.

- [ ] **Step 10.3: Commit**

```bash
git add fahrtenplaner/assets/style.css
git commit -m "ui/styles: amber accent styling for .auto-leg blocks"
```

---

## Task 11: CLAUDE.md — document `OptimizationResult` migration

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 11.1: Add a note about the new return type and entry point**

In `CLAUDE.md`, find the architecture section's optimizer subsection (around the line `### The optimizer (\`optimizer.py::optimize_day\`)`). Below the existing 4 phases, append:

```markdown
### Two optimization modes

`optimize_day` is the transit-only optimizer (returns a single `DayPlan`).
For car-mode (Auto-Anfahrt) support, the UI calls `optimize_with_modes(...)`,
which:

1. Calls `optimize_day` for the transit-only candidate.
2. If `max_car_minutes > 0` AND `home_station == dest_station`, also calls
   `optimize_day_car_mode` to find the best chain that *starts and ends* at
   a single car-park station within driving radius of home.
3. Returns an `OptimizationResult(winner, alternative)`. The winner is the
   plan with higher `net_euros` (gross − fuel cost). Tie → transit wins.

The UI shows the winner card prominently with the alternative as a
collapsible expander. `st.session_state.last_plan` is now
`Optional[OptimizationResult]` (was: `Optional[DayPlan]`).
```

- [ ] **Step 11.2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: note optimize_with_modes and OptimizationResult migration"
```

---

## Final verification

- [ ] **Run full test suite end-to-end**

Run:
```bash
uv run --no-project --python 3.12 --with-requirements requirements.txt --with pytest pytest tests/ -v
```
Expected: `38 passed`. All tests green; the 4 new ones (`TestDrivingInfo` × 1, `TestCarMode` × 3) appear in the output.

- [ ] **Final smoke-test the launcher with `--dev`**

Run:
```bash
uv run --no-project --python 3.12 --with-requirements requirements.txt python launcher.py --dev
```

Manual check in the opened webview window:
1. Sidebar shows new "Auto" expander with consumption + price defaults (7.0 / 1.79).
2. Optimization panel shows new `Max. Auto-Anfahrt (Min.)` 4th column.
3. With `Ankunft = Abfahrt` unchecked, the car-min input is disabled and shows the right tooltip.
4. With `Max. Auto-Anfahrt = 30` and a tour set that has early/late tours, click "Optimale Route berechnen".
5. Result shows winner card with either transit or car icon. If both modes succeed, alternative expander present below.
6. Toggle "Tagesroute auf Karte anzeigen" — for car plans, see amber dashed segments and the new "Auto" legend row.

Close the launcher when done.

- [ ] **Final commit (if any pending)**

```bash
git status   # should be clean
```

---

## Summary

| Step | Tests added | Tests total | Commits |
|---|---|---|---|
| Task 1 (models) | 0 | 34 | 1 |
| Task 2 (transit_client.driving_info) | 1 | 35 | 1 |
| Task 3 (optimize_day_car_mode) | 1 | 36 | 1 |
| Task 4 (optimize_with_modes) | 2 | 38 | 1 |
| Task 5 (state) | 0 | 38 | 1 |
| Task 6 (sidebar) | 0 | 38 | 1 |
| Task 7 (optimization UI) | 0 | 38 | 1 |
| Task 8 (render) | 0 | 38 | 1 |
| Task 9 (map) | 0 | 38 | 1 |
| Task 10 (CSS) | 0 | 38 | 1 |
| Task 11 (CLAUDE.md) | 0 | 38 | 1 |
| **Total** | **4 new tests** | **38** | **11 commits** |

Each commit independently revertable. Tests added next to behavior changes, not at the end.
