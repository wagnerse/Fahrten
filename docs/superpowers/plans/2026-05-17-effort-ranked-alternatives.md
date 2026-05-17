# Effort-Ranked Alternatives Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **⛔ HARD RULE (project-specific, overrides skill defaults):** Do **NOT** run `git add`, `git commit`, or `git push` at any point during execution. The CLAUDE.md hard rule applies: only the human user authorizes git history changes, and only with the literal word "commit" or "push". Leave every task's changes as uncommitted working-tree modifications. Subagent prompts must explicitly include "do not commit".

**Goal:** Surface up to 5 effort-ranked alternative day plans alongside the existing max-revenue winner, so the user can pick a plan with a better revenue-per-hour ratio when the optimizer's winner is exhausting.

**Architecture:** The DAG-DP in `optimize_day` already encodes "best chain ending at tour j" for every j. We expose those by-end-tour chains as candidates, merge transit + car-mode candidates, dedupe + filter + sort by overhead time, and pass them through `OptimizationResult.efficiency_options` to a new compact list section in the result UI.

**Tech Stack:** Python 3.12, Streamlit, pytest, `uv`. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-05-17-effort-ranked-alternatives-design.md`

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `fahrtenplaner/models.py` | modify | Add `DayPlan.overhead_duration`, `DayPlan.euros_per_hour`. Extend `OptimizationResult` with `efficiency_options: list[DayPlan]`. |
| `fahrtenplaner/optimizer.py` | modify | New constants, new `_enumerate_chain_candidates`, new `_build_efficiency_options`. Change return types of `optimize_day` and `optimize_day_car_mode`. Update `optimize_with_modes`. |
| `fahrtenplaner/ui/render.py` | modify | New `_render_efficiency_options`. Call it from `render_result` after winner/alternative. |
| `fahrtenplaner/assets/style.css` | modify | New `.efficiency-list` block of styles, appended after the bahn-row block (~line 1310). |
| `tests/test_optimizer.py` | modify | Update existing call sites of `optimize_day` / `optimize_day_car_mode` to unpack the new tuple. Add `TestDayPlanMetrics` and `TestEfficiencyOptions`. |

All code comments in English. UI strings in German.

---

## Task 1: Add `DayPlan.overhead_duration` property

**Files:**
- Modify: `fahrtenplaner/models.py`
- Test: `tests/test_optimizer.py` (new class `TestDayPlanMetrics`)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_optimizer.py`:

```python
# ---------------------------------------------------------------------------
# DayPlan metrics (overhead_duration, euros_per_hour)
# ---------------------------------------------------------------------------

class TestDayPlanMetrics:
    """Cover the new DayPlan properties used by effort-ranked alternatives."""

    def test_overhead_duration_sums_only_connections(self):
        """Tour duration is paid time — only connections + car legs count as overhead."""
        from models import ChainLink, Connection, Leg

        # outbound: 1h, transfer: 30min, inbound: 45min  → 2h15 overhead
        outbound = Connection(legs=[Leg(
            departure_station="A", departure_time=datetime(2026, 6, 1, 5, 0),
            arrival_station="B",   arrival_time=  datetime(2026, 6, 1, 6, 0),
            line="RE1",
        )])
        transfer = Connection(legs=[Leg(
            departure_station="B", departure_time=datetime(2026, 6, 1, 8, 0),
            arrival_station="C",   arrival_time=  datetime(2026, 6, 1, 8, 30),
            line="RE2",
        )])
        inbound = Connection(legs=[Leg(
            departure_station="D", departure_time=datetime(2026, 6, 1, 12, 0),
            arrival_station="A",   arrival_time=  datetime(2026, 6, 1, 12, 45),
            line="RE3",
        )])
        tour_a = make_tour(1, "06:00", "B", "08:00", "B", 30.0)
        tour_b = make_tour(2, "08:30", "C", "12:00", "D", 50.0)

        plan = DayPlan()
        plan.chain.extend([
            ChainLink(type="outbound", connection=outbound),
            ChainLink(type="tour", tour=tour_a),
            ChainLink(type="transfer", connection=transfer),
            ChainLink(type="tour", tour=tour_b),
            ChainLink(type="inbound", connection=inbound),
        ])
        assert plan.overhead_duration == timedelta(hours=2, minutes=15)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --with pytest pytest tests/test_optimizer.py::TestDayPlanMetrics::test_overhead_duration_sums_only_connections -v`
Expected: FAIL with `AttributeError: 'DayPlan' object has no attribute 'overhead_duration'`

- [ ] **Step 3: Implement the property in `models.py`**

Add inside the `DayPlan` class, immediately after the `total_costs` property:

```python
    @property
    def overhead_duration(self) -> timedelta:
        """Aufwand: sum of all non-tour durations in the chain.

        Outbound + transfer + inbound connection durations are counted, plus
        any car-leg minutes. Paid tour durations are NOT counted — the user
        considers those productive time, not effort cost.
        """
        total = timedelta(0)
        for link in self.chain:
            if link.connection and link.connection.duration:
                total += link.connection.duration
            if link.car_leg is not None:
                total += timedelta(minutes=link.car_leg.minutes)
        return total
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --with pytest pytest tests/test_optimizer.py::TestDayPlanMetrics::test_overhead_duration_sums_only_connections -v`
Expected: PASS.

- [ ] **Step 5: Add the car-leg test**

Append to `TestDayPlanMetrics`:

```python
    def test_overhead_duration_includes_car_legs(self):
        """car_outbound + car_inbound minutes are part of overhead."""
        from models import CarLeg, ChainLink

        car_out = CarLeg(from_station="Home", to_station="Park",
                         minutes=30, km=32.0, cost=5.0)
        car_in  = CarLeg(from_station="Park", to_station="Home",
                         minutes=35, km=32.0, cost=5.0)  # slightly longer return
        tour = make_tour(1, "06:00", "Park", "08:00", "Park", 50.0)

        plan = DayPlan()
        plan.chain.extend([
            ChainLink(type="car_outbound", car_leg=car_out),
            ChainLink(type="tour", tour=tour),
            ChainLink(type="car_inbound", car_leg=car_in),
        ])
        # 30 + 35 = 65 min; tour duration excluded
        assert plan.overhead_duration == timedelta(minutes=65)
```

- [ ] **Step 6: Run test to verify it passes**

Run: `uv run --with pytest pytest tests/test_optimizer.py::TestDayPlanMetrics -v`
Expected: 2 PASS.

---

## Task 2: Add `DayPlan.euros_per_hour` property

**Files:**
- Modify: `fahrtenplaner/models.py`
- Test: `tests/test_optimizer.py::TestDayPlanMetrics`

- [ ] **Step 1: Write the failing test**

Append to `TestDayPlanMetrics`:

```python
    def test_euros_per_hour_division(self):
        """net_euros / overhead_hours; verify a known value."""
        from models import ChainLink, Connection, Leg

        # 4h13 overhead (253 minutes), 42.12 € net → ≈ 9.99 €/h
        outbound = Connection(legs=[Leg(
            departure_station="A", departure_time=datetime(2026, 6, 1, 5, 0),
            arrival_station="B",   arrival_time=  datetime(2026, 6, 1, 7, 0),
            line="RE1",
        )])  # 2h
        inbound = Connection(legs=[Leg(
            departure_station="C", departure_time=datetime(2026, 6, 1, 11, 0),
            arrival_station="A",   arrival_time=  datetime(2026, 6, 1, 13, 13),
            line="RE2",
        )])  # 2h13
        tour = make_tour(1, "07:00", "B", "11:00", "C", 42.12)

        plan = DayPlan()
        plan.chain.extend([
            ChainLink(type="outbound", connection=outbound),
            ChainLink(type="tour", tour=tour),
            ChainLink(type="inbound", connection=inbound),
        ])
        # 42.12 / (253/60) = 42.12 / 4.2167 ≈ 9.9876
        assert plan.euros_per_hour == pytest.approx(9.99, abs=0.01)

    def test_euros_per_hour_zero_when_no_overhead(self):
        """Degenerate plan with no connections and no car legs returns 0.0."""
        from models import ChainLink

        tour = make_tour(1, "06:00", "A", "08:00", "A", 10.0)
        plan = DayPlan()
        plan.chain.append(ChainLink(type="tour", tour=tour))
        assert plan.euros_per_hour == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --with pytest pytest tests/test_optimizer.py::TestDayPlanMetrics::test_euros_per_hour_division -v`
Expected: FAIL with `AttributeError: 'DayPlan' object has no attribute 'euros_per_hour'`.

- [ ] **Step 3: Implement the property in `models.py`**

Add immediately after `overhead_duration` in the `DayPlan` class:

```python
    @property
    def euros_per_hour(self) -> float:
        """Net euros divided by overhead hours. 0.0 when overhead is zero
        (degenerate case — never happens in real chains because outbound
        always carries at least a few minutes of platform time)."""
        hours = self.overhead_duration.total_seconds() / 3600
        return self.net_euros / hours if hours > 0 else 0.0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --with pytest pytest tests/test_optimizer.py::TestDayPlanMetrics -v`
Expected: 4 PASS.

---

## Task 3: Extend `OptimizationResult` with `efficiency_options` field

**Files:**
- Modify: `fahrtenplaner/models.py`

This is a pure dataclass field addition with a default; no test needed beyond the integration tests later.

- [ ] **Step 1: Add the field**

In `models.py`, change the `OptimizationResult` dataclass:

```python
@dataclass
class OptimizationResult:
    """A primary plan + an optional alternative for side-by-side comparison."""
    winner: DayPlan
    alternative: Optional["DayPlan"] = None
    efficiency_options: list["DayPlan"] = field(default_factory=list)
    latest_return_target: Optional[datetime] = None
```

- [ ] **Step 2: Run the existing test suite to confirm nothing broke**

Run: `uv run --with pytest pytest tests/ -q`
Expected: All existing tests still pass. (`field` is already imported at the top of `models.py`.)

---

## Task 4: Add optimizer constants

**Files:**
- Modify: `fahrtenplaner/optimizer.py`

- [ ] **Step 1: Add the constants**

In `optimizer.py`, find the existing constants block near the top (it contains `MIN_TRANSFER_MINUTES`, `TIGHT_TRANSFER_MINUTES`, etc.). Add right after:

```python
# Effort-ranked alternatives — surfaced alongside the max-revenue winner so
# the user can pick a plan with a better revenue-per-hour ratio.
EFFICIENCY_TOP_K: int = 5            # how many alternatives to show
EFFICIENCY_MIN_NET_EUROS: float = 10.0  # drop micro-trips that ruin signal/noise
```

- [ ] **Step 2: Verify the module still imports**

Run: `uv run python -c "import sys; sys.path.insert(0, 'fahrtenplaner'); import optimizer; print(optimizer.EFFICIENCY_TOP_K, optimizer.EFFICIENCY_MIN_NET_EUROS)"`
Expected: prints `5 10.0`.

---

## Task 5: Change `optimize_day` to return `(DayPlan, list[DayPlan])`

**Files:**
- Modify: `fahrtenplaner/optimizer.py`
- Modify: `tests/test_optimizer.py` (helper `run_optimizer` at line ~158 unpacks new tuple)

This is a breaking signature change. We must update both the function and all direct callers in the same task so the suite stays green.

- [ ] **Step 1: Add the new helper `_enumerate_chain_candidates` in `optimizer.py`**

Place it directly under `_find_best_chain_end` (around line 311):

```python
def _enumerate_chain_candidates(
    tours: list[Tour],
    outbound: list[Optional[Connection]],
    edge: list[list[Optional[Connection]]],
    inbound: list[Optional[Connection]],
    dp: list[float],
    pred: list[int],
) -> list[DayPlan]:
    """Reconstruct one DayPlan per valid end-tour.

    The DP table already encodes 'best chain ending at j' for every j; this
    just reads each entry out as its own materialized chain. No extra search.
    Skips entries with NEG_INF (no chain ends here) or missing inbound.
    """
    plans: list[DayPlan] = []
    for j, val in enumerate(dp):
        if val == NEG_INF or inbound[j] is None:
            continue
        chain_indices = _reconstruct_chain(pred, j)
        plans.append(
            _build_dayplan(tours, chain_indices, outbound, edge, inbound)
        )
    return plans
```

- [ ] **Step 2: Change `optimize_day` to return a tuple**

Replace the tail of `optimize_day` (from the comment `# ----- DP and reconstruction -----` through the end of the function) with:

```python
    # ----- DP and reconstruction --------------------------------------------
    report(0.78, "Optimiere Tourenkette (DAG-DP)...")
    dp, pred = _run_dag_dp(tours, outbound, edge)
    report(0.88, "Beste Route wird rekonstruiert...")

    candidates = _enumerate_chain_candidates(
        tours, outbound, edge, inbound, dp, pred,
    )

    best_j = _find_best_chain_end(dp, inbound)
    if best_j == -1:
        report(1.0, "Keine gültige Tourenkette gefunden.")
        return DayPlan(), candidates
    chain_indices = _reconstruct_chain(pred, best_j)

    # ----- DayPlan synthesis ------------------------------------------------
    report(0.93, "Tagesplan wird zusammengestellt...")
    plan = _build_dayplan(tours, chain_indices, outbound, edge, inbound)
    report(
        1.0,
        f"Fertig! {plan.num_tours} Touren, {plan.total_euros:.2f}€ "
        f"({api_calls} API-Calls)",
    )
    return plan, candidates
```

Also update the function's return-type annotation:

```python
def optimize_day(
    tours: list[Tour],
    home_station: str,
    dest_station: str,
    earliest_departure: datetime,
    latest_return: datetime,
    progress_callback: ProgressCb = None,
    max_transfer_gap_hours: float = MAX_TRANSFER_GAP_HOURS,
) -> tuple[DayPlan, list[DayPlan]]:
```

And the early-return branches: the function has two early `return DayPlan()` statements (at the "tours is empty" guard and at the "home station not found" / "dest station not found" branches). Each must now return `DayPlan(), []`. Update all of them:

```python
    if not tours:
        return DayPlan(), []
```
```python
    if not home_id:
        report(1.0, f"Station '{home_station}' nicht gefunden!")
        return DayPlan(), []
    if not dest_id:
        report(1.0, f"Station '{dest_station}' nicht gefunden!")
        return DayPlan(), []
```
```python
    tours, outbound = _filter_and_sort_reachable(tours, outbound)
    if not tours:
        report(1.0, "Keine Tour von zuhause erreichbar!")
        return DayPlan(), []
```

- [ ] **Step 3: Update the direct caller in `tests/test_optimizer.py`**

The `run_optimizer` helper (line 158-166) returns `optimize_day(...)`. Change it to:

```python
        from optimizer import optimize_day
        plan, _candidates = optimize_day(
            tours=tours,
            home_station=home,
            dest_station=dest,
            earliest_departure=datetime.combine(DAY, time(h1, m1)),
            latest_return=datetime.combine(DAY, time(h2, m2)),
            max_transfer_gap_hours=max_gap_hours,
        )
        return plan
```

(The helper continues to return just the `DayPlan` so all existing tests that call `run_optimizer(...)` keep working unchanged.)

- [ ] **Step 4: Update the call site in `optimize_with_modes`**

In `optimizer.py`, find the `optimize_with_modes` function (~line 643). Change:

```python
    transit_plan = optimize_day(
        tours, home_station, dest_station,
        earliest_departure, latest_return,
        progress_callback=progress_callback,
        max_transfer_gap_hours=max_transfer_gap_hours,
    )
```

to:

```python
    transit_plan, transit_candidates = optimize_day(
        tours, home_station, dest_station,
        earliest_departure, latest_return,
        progress_callback=progress_callback,
        max_transfer_gap_hours=max_transfer_gap_hours,
    )
```

(We are not wiring `transit_candidates` into the result yet — that happens in Task 9 — but capturing it now keeps the call legal and avoids a tuple-as-DayPlan bug.)

- [ ] **Step 5: Run the full test suite**

Run: `uv run --with pytest pytest tests/ -q`
Expected: All transit-mode tests pass. **One car-mode test will likely fail** (`test_winner_is_decided_by_net_euros`) because its `patch("optimizer.optimize_day", return_value=transit_plan)` injects a `DayPlan` where `optimize_with_modes` now expects a tuple. Fix that patch in Step 6.

- [ ] **Step 6: Fix the mock in `test_winner_is_decided_by_net_euros`**

In `tests/test_optimizer.py` around line 784, change:

```python
        with patch("optimizer.optimize_day", return_value=transit_plan), \
             patch("optimizer.optimize_day_car_mode", return_value=car_plan):
```

to:

```python
        with patch("optimizer.optimize_day", return_value=(transit_plan, [])), \
             patch("optimizer.optimize_day_car_mode", return_value=car_plan):
```

(We update the car-mode patch in Task 6.)

- [ ] **Step 7: Fix the mock in `test_car_mode_skipped_when_dest_differs`**

Around line 805, change:

```python
        with patch("optimizer.optimize_day", return_value=DayPlan()) as transit_mock, \
             patch("optimizer.optimize_day_car_mode") as car_mock:
```

to:

```python
        with patch("optimizer.optimize_day", return_value=(DayPlan(), [])) as transit_mock, \
             patch("optimizer.optimize_day_car_mode") as car_mock:
```

- [ ] **Step 8: Run the full test suite**

Run: `uv run --with pytest pytest tests/ -q`
Expected: All tests pass except those that depend on `optimize_day_car_mode`'s old signature (which is still a single DayPlan). Those are fixed in Task 6.

If the test `test_winner_is_decided_by_net_euros` is still failing because `optimize_day_car_mode` is also patched and the production `optimize_with_modes` doesn't yet unpack a tuple from it, that's expected — Task 6 fixes it.

---

## Task 6: Change `optimize_day_car_mode` to return `(DayPlan, list[DayPlan])`

**Files:**
- Modify: `fahrtenplaner/optimizer.py`
- Modify: `tests/test_optimizer.py` (multiple call sites)

- [ ] **Step 1: Change the function**

In `optimizer.py`, find `optimize_day_car_mode` (~line 470). Two changes:

1. Update the return-type annotation:

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
) -> tuple[DayPlan, list[DayPlan]]:
```

2. Replace the loop body and tail. Find:

```python
    cost_per_km = (fuel_consumption / 100.0) * fuel_price
    best_plan = DayPlan()

    report(0.0, f"Auto-Modus: prüfe {len(candidates)} Kandidaten...")

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

        report(
            (candidates.index(candidate) + 1) / max(len(candidates), 1),
            f"Auto-Modus: {candidate} ({drive_min} min, {drive_km:.0f} km)",
        )

        plan = _build_car_chain_for_candidate(
            sorted_tours, edge, candidate, drive_min, drive_km, cost_per_km,
            earliest_departure, latest_return_hard, home_station, get_id,
        )
        if plan.net_euros > best_plan.net_euros:
            best_plan = plan

    if best_plan.num_tours > 0:
        report(1.0, f"Auto-Modus fertig: {best_plan.total_euros:.2f} € brutto")
    else:
        report(1.0, "Auto-Modus: keine Kette gefunden")

    return best_plan
```

Replace with:

```python
    cost_per_km = (fuel_consumption / 100.0) * fuel_price
    best_plan = DayPlan()
    car_candidates: list[DayPlan] = []

    report(0.0, f"Auto-Modus: prüfe {len(candidates)} Kandidaten...")

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

        report(
            (candidates.index(candidate) + 1) / max(len(candidates), 1),
            f"Auto-Modus: {candidate} ({drive_min} min, {drive_km:.0f} km)",
        )

        plan = _build_car_chain_for_candidate(
            sorted_tours, edge, candidate, drive_min, drive_km, cost_per_km,
            earliest_departure, latest_return_hard, home_station, get_id,
        )
        if plan.num_tours == 0:
            continue
        car_candidates.append(plan)
        if plan.net_euros > best_plan.net_euros:
            best_plan = plan

    if best_plan.num_tours > 0:
        report(1.0, f"Auto-Modus fertig: {best_plan.total_euros:.2f} € brutto")
    else:
        report(1.0, "Auto-Modus: keine Kette gefunden")

    return best_plan, car_candidates
```

Also update the early-return branches:

```python
    if not tours or max_car_minutes <= 0:
        return DayPlan(), []
```
```python
    if not home_id:
        return DayPlan(), []
```

- [ ] **Step 2: Update the call site in `optimize_with_modes`**

In `optimizer.py`, change:

```python
    car_plan: DayPlan = DayPlan()
    if max_car_minutes > 0 and stations_match(home_station, dest_station):
        car_plan = optimize_day_car_mode(
            tours, home_station,
            earliest_departure, latest_return,
            max_car_minutes, fuel_consumption, fuel_price,
            progress_callback=progress_callback,
            max_transfer_gap_hours=max_transfer_gap_hours,
        )
```

to:

```python
    car_plan: DayPlan = DayPlan()
    car_candidates: list[DayPlan] = []
    if max_car_minutes > 0 and stations_match(home_station, dest_station):
        car_plan, car_candidates = optimize_day_car_mode(
            tours, home_station,
            earliest_departure, latest_return,
            max_car_minutes, fuel_consumption, fuel_price,
            progress_callback=progress_callback,
            max_transfer_gap_hours=max_transfer_gap_hours,
        )
```

- [ ] **Step 3: Fix the direct call in `test_finds_chain_starting_and_ending_at_same_station`**

In `tests/test_optimizer.py` around line 735, change:

```python
            plan = optimize_day_car_mode(
                tours=[tour_a, tour_b],
                ...
            )
```

to:

```python
            plan, _candidates = optimize_day_car_mode(
                tours=[tour_a, tour_b],
                ...
            )
```

(Leave the rest of the test body unchanged — the assertions remain on `plan`.)

- [ ] **Step 4: Fix the direct call in `test_finds_chain_with_transit_return_to_car`**

In `tests/test_optimizer.py` around line 838, change:

```python
            plan = optimize_day_car_mode(
                tours=[tour],
                ...
            )
```

to:

```python
            plan, _candidates = optimize_day_car_mode(
                tours=[tour],
                ...
            )
```

- [ ] **Step 5: Fix the mock in `test_winner_is_decided_by_net_euros`**

In `tests/test_optimizer.py` around line 785, change:

```python
        with patch("optimizer.optimize_day", return_value=(transit_plan, [])), \
             patch("optimizer.optimize_day_car_mode", return_value=car_plan):
```

to:

```python
        with patch("optimizer.optimize_day", return_value=(transit_plan, [])), \
             patch("optimizer.optimize_day_car_mode", return_value=(car_plan, [])):
```

- [ ] **Step 6: Run the full test suite**

Run: `uv run --with pytest pytest tests/ -q`
Expected: All existing tests pass. The two new `TestDayPlanMetrics` tests from Tasks 1 and 2 also pass.

---

## Task 7: Implement `_build_efficiency_options` with full TDD coverage

**Files:**
- Modify: `fahrtenplaner/optimizer.py`
- Modify: `tests/test_optimizer.py` (new class `TestEfficiencyOptions`)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_optimizer.py`:

```python
# ---------------------------------------------------------------------------
# Effort-ranked alternatives — _build_efficiency_options
# ---------------------------------------------------------------------------

def _single_tour_plan(
    tour_nr: int, dep_station: str, dep_time: str, arr_station: str,
    arr_time: str, euros: float,
    outbound_min: int = 60, inbound_min: int = 60,
) -> DayPlan:
    """Build a single-tour DayPlan with synthesised outbound + inbound for tests.

    `outbound_min` and `inbound_min` set the connection durations so the test
    can control the resulting overhead_duration.
    """
    from models import ChainLink, Connection, Leg

    tour = make_tour(tour_nr, dep_time, dep_station, arr_time, arr_station, euros)
    out_dep = tour.departure_dt - timedelta(minutes=outbound_min)
    in_arr  = tour.arrival_dt + timedelta(minutes=inbound_min)

    outbound = Connection(legs=[Leg(
        departure_station="HOME", departure_time=out_dep,
        arrival_station=dep_station, arrival_time=tour.departure_dt,
        line="RE1",
    )])
    inbound = Connection(legs=[Leg(
        departure_station=arr_station, departure_time=tour.arrival_dt,
        arrival_station="HOME", arrival_time=in_arr,
        line="RE2",
    )])
    plan = DayPlan()
    plan.chain.extend([
        ChainLink(type="outbound", connection=outbound),
        ChainLink(type="tour", tour=tour),
        ChainLink(type="inbound", connection=inbound),
    ])
    return plan


class TestEfficiencyOptions:
    """_build_efficiency_options: filter → dedupe → exclude → sort → truncate."""

    def test_returns_top_5_sorted_ascending_by_overhead(self):
        from optimizer import _build_efficiency_options

        # 7 candidates with increasing overhead. Top 5 expected, in order.
        candidates = [
            _single_tour_plan(100 + i, "A", "08:00", "B", "09:00",
                              euros=30.0,
                              outbound_min=10 + i * 10, inbound_min=10)
            for i in range(7)
        ]
        result = _build_efficiency_options(candidates, excluded=[])
        assert len(result) == 5
        overheads = [p.overhead_duration for p in result]
        assert overheads == sorted(overheads)

    def test_excludes_winner_and_alternative(self):
        from optimizer import _build_efficiency_options

        winner = _single_tour_plan(200, "A", "08:00", "B", "09:00",
                                    euros=40.0, outbound_min=30, inbound_min=30)
        alt    = _single_tour_plan(201, "A", "10:00", "B", "11:00",
                                    euros=35.0, outbound_min=40, inbound_min=40)
        other  = _single_tour_plan(202, "A", "12:00", "B", "13:00",
                                    euros=30.0, outbound_min=20, inbound_min=20)

        result = _build_efficiency_options(
            [winner, alt, other], excluded=[winner, alt],
        )
        tour_nrs = [p.tours[0].tour_nr for p in result]
        assert tour_nrs == [202]

    def test_deduplicates_identical_tour_sequences(self):
        from optimizer import _build_efficiency_options

        # Same tour number, same has_car_legs flag → collapsed.
        a = _single_tour_plan(300, "A", "08:00", "B", "09:00",
                              euros=30.0, outbound_min=30, inbound_min=30)
        b = _single_tour_plan(300, "A", "08:00", "B", "09:00",
                              euros=30.0, outbound_min=45, inbound_min=45)  # different overhead
        result = _build_efficiency_options([a, b], excluded=[])
        assert len(result) == 1
        # First-seen wins (the 60-min total overhead one)
        assert result[0].overhead_duration == timedelta(minutes=60)

    def test_drops_plans_below_min_net_euros(self):
        from optimizer import _build_efficiency_options, EFFICIENCY_MIN_NET_EUROS

        assert EFFICIENCY_MIN_NET_EUROS == 10.0  # guard against silent re-tuning

        tiny = _single_tour_plan(400, "A", "08:00", "B", "09:00",
                                 euros=5.0, outbound_min=5, inbound_min=5)
        big  = _single_tour_plan(401, "A", "10:00", "B", "11:00",
                                 euros=40.0, outbound_min=60, inbound_min=60)
        result = _build_efficiency_options([tiny, big], excluded=[])
        tour_nrs = [p.tours[0].tour_nr for p in result]
        assert tour_nrs == [401]

    def test_empty_list_when_no_candidates(self):
        from optimizer import _build_efficiency_options
        assert _build_efficiency_options([], excluded=[]) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --with pytest pytest tests/test_optimizer.py::TestEfficiencyOptions -v`
Expected: All 5 FAIL with `ImportError: cannot import name '_build_efficiency_options'`.

- [ ] **Step 3: Implement `_build_efficiency_options`**

In `optimizer.py`, add this function. Good location: right after `_enumerate_chain_candidates` (which you added in Task 5):

```python
def _plan_identity(plan: DayPlan) -> tuple:
    """Stable key for deduplication and exclusion comparison.

    Two plans are 'the same' when they hit the same tour numbers in the same
    order AND share the same mode (transit vs. car). Different routings
    (e.g. different transfer waits) between the same tours collapse to one
    entry — the user only sees the chain, not the underlying routing.
    """
    return (
        tuple(t.tour_nr for t in plan.tours),
        plan.has_car_legs,
    )


def _build_efficiency_options(
    candidates: list[DayPlan],
    excluded: list[DayPlan],
) -> list[DayPlan]:
    """Filter, dedupe, exclude, sort, truncate.

    Returns the top EFFICIENCY_TOP_K plans ordered by `overhead_duration`
    ascending (tie-break: higher net_euros first).
    """
    excluded_ids = {_plan_identity(p) for p in excluded if p.num_tours > 0}

    seen: set[tuple] = set()
    keep: list[DayPlan] = []
    for plan in candidates:
        if plan.num_tours == 0:
            continue
        if plan.net_euros < EFFICIENCY_MIN_NET_EUROS:
            continue
        ident = _plan_identity(plan)
        if ident in excluded_ids or ident in seen:
            continue
        seen.add(ident)
        keep.append(plan)

    keep.sort(key=lambda p: (p.overhead_duration, -p.net_euros))
    return keep[:EFFICIENCY_TOP_K]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --with pytest pytest tests/test_optimizer.py::TestEfficiencyOptions -v`
Expected: 5 PASS.

- [ ] **Step 5: Run the full suite**

Run: `uv run --with pytest pytest tests/ -q`
Expected: Everything still green.

---

## Task 8: Wire `efficiency_options` into `optimize_with_modes`

**Files:**
- Modify: `fahrtenplaner/optimizer.py`
- Modify: `tests/test_optimizer.py` (add integration test)

- [ ] **Step 1: Write the failing integration test**

Append to `TestEfficiencyOptions` in `tests/test_optimizer.py`:

```python
    def test_optimize_with_modes_populates_efficiency_options(self):
        """End-to-end: optimize_with_modes returns alternatives in efficiency_options."""
        from optimizer import optimize_with_modes

        # Three single-tour candidates from transit mode; winner is the highest
        # net_euros, the other two should appear in efficiency_options sorted
        # by overhead ascending.
        winner_plan = _single_tour_plan(500, "A", "08:00", "B", "09:00",
                                         euros=50.0, outbound_min=120, inbound_min=120)
        cheap_plan  = _single_tour_plan(501, "A", "10:00", "B", "11:00",
                                         euros=40.0, outbound_min=20, inbound_min=20)
        mid_plan    = _single_tour_plan(502, "A", "12:00", "B", "13:00",
                                         euros=30.0, outbound_min=60, inbound_min=60)

        with patch("optimizer.optimize_day",
                   return_value=(winner_plan, [winner_plan, cheap_plan, mid_plan])), \
             patch("optimizer.stations_match", return_value=False):
            result = optimize_with_modes(
                tours=[],
                home_station="Prenzlau", dest_station="Stralsund",  # ≠ → no car mode
                earliest_departure=datetime.combine(DAY, time(4, 0)),
                latest_return=datetime.combine(DAY, time(23, 59)),
                max_car_minutes=0,
                fuel_consumption=7.0,
                fuel_price=1.79,
            )

        assert result.winner.tours[0].tour_nr == 500
        # cheap_plan has 40 min overhead, mid_plan has 120 min — cheap first
        nrs = [p.tours[0].tour_nr for p in result.efficiency_options]
        assert nrs == [501, 502]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run --with pytest pytest tests/test_optimizer.py::TestEfficiencyOptions::test_optimize_with_modes_populates_efficiency_options -v`
Expected: FAIL — `efficiency_options` is still `[]`.

- [ ] **Step 3: Update `optimize_with_modes` to assemble the list**

In `optimizer.py`, find `optimize_with_modes`. Replace its tail (from `candidates = [p for p in (transit_plan, car_plan) if p.num_tours > 0]`) with:

```python
    plans = [p for p in (transit_plan, car_plan) if p.num_tours > 0]
    if not plans:
        return OptimizationResult(
            winner=DayPlan(), alternative=None,
            efficiency_options=[],
            latest_return_target=latest_return,
        )

    # Sort by net euros desc, tie-break: transit wins (no car legs).
    plans.sort(key=lambda p: (-p.net_euros, p.has_car_legs))
    winner = plans[0]
    alternative = plans[1] if len(plans) > 1 else None

    efficiency_options = _build_efficiency_options(
        candidates=transit_candidates + car_candidates,
        excluded=[winner] + ([alternative] if alternative else []),
    )
    return OptimizationResult(
        winner=winner, alternative=alternative,
        efficiency_options=efficiency_options,
        latest_return_target=latest_return,
    )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run --with pytest pytest tests/test_optimizer.py::TestEfficiencyOptions -v`
Expected: 6 PASS (5 unit + 1 integration).

- [ ] **Step 5: Run the full suite**

Run: `uv run --with pytest pytest tests/ -q`
Expected: All tests green. This is the natural pause point — the backend feature is complete and tested. Tasks 9–10 add UI.

---

## Task 9: Render the effort-ranked options in `ui/render.py`

**Files:**
- Modify: `fahrtenplaner/ui/render.py`

UI is not directly test-covered in this project; verification is by smoke-test in the browser.

- [ ] **Step 1: Add the helper functions**

In `fahrtenplaner/ui/render.py`, add these helpers above `render_result` (which is currently at the bottom of the file, around line 530):

```python
# --------------------------------------------------------------------------- #
# Effort-ranked alternatives — compact list rendered under winner/alternative
# --------------------------------------------------------------------------- #

def _fmt_overhead(td: timedelta) -> str:
    """4h13 / 45 min — shorter than _fmt_duration for the compact row."""
    total_min = max(0, int(td.total_seconds() / 60))
    h, m = divmod(total_min, 60)
    if h == 0:
        return f"{m} min"
    return f"{h}h{m:02d}"


def _short_route_label(plan: DayPlan) -> str:
    """First tour number + 'Station → Station' across the whole chain."""
    if not plan.tours:
        return "—"
    first = plan.tours[0]
    last = plan.tours[-1]
    nrs = " · ".join(str(t.tour_nr) for t in plan.tours)
    return f"{nrs} — {first.departure_station} → {last.arrival_station}"


def _render_efficiency_row(
    plan: DayPlan,
    idx: int,
    fuel_consumption: float,
    fuel_price: float,
    latest_return_target: Optional[datetime],
) -> None:
    """One compact row + Details disclosure reusing _render_details."""
    overhead_str = _fmt_overhead(plan.overhead_duration)
    eur_per_h_str = (
        f"{plan.euros_per_hour:.1f}".replace(".", ",") + " €/h"
        if plan.overhead_duration.total_seconds() > 0 else "—"
    )
    net_str = _fmt_eur(plan.net_euros)
    label = _short_route_label(plan)

    _html(f"""
        <article class="eff-row">
          <div class="eff-row__lead">
            <span class="eff-row__overhead">⏱ {overhead_str}</span>
            <span class="eff-row__label">{label}</span>
          </div>
          <div class="eff-row__meta">
            <span class="eff-row__net">{net_str}</span>
            <span class="eff-row__sep">·</span>
            <span class="eff-row__rate">{eur_per_h_str}</span>
          </div>
        </article>
        """)

    kind = f"eff_{idx}"
    expanded_key = f"bahn_details_expanded_{kind}"
    expanded = st.session_state.get(expanded_key, False)
    chevron = "▴" if expanded else "▾"
    if st.button(
        f"Details {chevron}",
        key=f"bahn_details_btn_{kind}",
        use_container_width=True,
    ):
        st.session_state[expanded_key] = not expanded
        st.rerun()
    if expanded:
        _render_details(plan, kind, fuel_consumption, fuel_price)


def _render_efficiency_options(
    options: list[DayPlan],
    fuel_consumption: float,
    fuel_price: float,
    latest_return_target: Optional[datetime],
) -> None:
    if not options:
        return
    _eyebrow(
        "Weitere Optionen — nach Aufwand sortiert",
        f'<span class="result-eyebrow__count">{len(options)} Optionen</span>',
    )
    for idx, plan in enumerate(options):
        _render_efficiency_row(
            plan, idx, fuel_consumption, fuel_price, latest_return_target,
        )
```

- [ ] **Step 2: Call it from `render_result`**

Replace `render_result` at the bottom of the file:

```python
def render_result(result: OptimizationResult) -> None:
    """Render winner row + (optional) alternative row + effort-ranked options."""
    fuel_consumption = float(st.session_state.get("fuel_consumption", 7.0))
    fuel_price = float(st.session_state.get("fuel_price", 1.79))
    latest_return_target = result.latest_return_target

    _render_plan_section(
        result.winner, "winner", fuel_consumption, fuel_price, latest_return_target,
    )

    if result.has_alternative:
        _render_plan_section(
            result.alternative, "alternative",
            fuel_consumption, fuel_price, latest_return_target,
        )

    _render_efficiency_options(
        result.efficiency_options, fuel_consumption, fuel_price, latest_return_target,
    )
```

- [ ] **Step 3: Verify imports**

The new code uses `timedelta`, `Optional`, and `DayPlan`. All three are already imported at the top of `render.py` (`from datetime import datetime, timedelta`, `from typing import Optional`, `from models import ChainLink, DayPlan, OptimizationResult`). No import change needed.

- [ ] **Step 4: Smoke-test in the browser**

Run: `./dev.sh`

In the browser:
1. Pick a date that has many tours (e.g. 2026-06-01, the Prenzlau/Brandenburg+MV scenario from the screenshot).
2. Click "Optimale Route berechnen".
3. Verify the winner card renders unchanged.
4. Below it (and below the Auto alternative, if present), verify the new "Weitere Optionen — nach Aufwand sortiert" section appears with up to 5 rows.
5. Verify each row shows ⏱ overhead-time, tour-nr + route, brutto, and €/h.
6. Click "Details ▾" on a row → full chain blocks + map toggle render (same look as winner details).
7. Toggle expanded/collapsed twice to confirm state persistence across the rerun.

If steps 1–3 work but the new section is missing, confirm `st.session_state.last_plan` is an `OptimizationResult` with `efficiency_options` populated (the tour-count badge in the eyebrow exposes the count).

Stop the dev server (Ctrl+C) when done.

---

## Task 10: Add CSS for the compact effort row

**Files:**
- Modify: `fahrtenplaner/assets/style.css`

- [ ] **Step 1: Append the new style block**

Append at the very end of `fahrtenplaner/assets/style.css`:

```css
/* =========================================================================
 * Effort-ranked alternatives — compact rows under the winner/alternative
 * ========================================================================= */

.eff-row {
  display: grid;
  grid-template-columns: 1fr auto;
  align-items: center;
  gap: 1rem;
  padding: 0.75rem 1rem;
  border: 1px solid var(--ink-faint);
  border-radius: 8px;
  background: var(--surface);
  margin-top: 0.5rem;
}

.eff-row__lead {
  display: flex;
  align-items: baseline;
  gap: 0.75rem;
  min-width: 0;
}

.eff-row__overhead {
  font-variant-numeric: tabular-nums;
  font-weight: 600;
  color: var(--ink);
  white-space: nowrap;
}

.eff-row__label {
  color: var(--ink-soft);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.eff-row__meta {
  display: flex;
  align-items: baseline;
  gap: 0.5rem;
  color: var(--ink-soft);
  font-variant-numeric: tabular-nums;
}

.eff-row__net {
  font-weight: 600;
  color: var(--ink);
}

.eff-row__sep { opacity: 0.4; }
```

These rules reuse the existing `--ink`, `--ink-soft`, `--ink-faint`, and `--surface` CSS variables that the bahn-row block (lines ~982+) already defines, so the look stays consistent with the rest of the result UI.

- [ ] **Step 2: Verify variables exist**

Run: `grep -n "\-\-ink-soft\|\-\-ink-faint\|\-\-surface\b" fahrtenplaner/assets/style.css | head -5`
Expected: each variable is defined somewhere near the top of `style.css` in the `:root` block. If `--ink-faint` is missing, substitute the closest existing token (most likely `--ink-soft` with reduced opacity, e.g. `rgba(0,0,0,0.08)`).

- [ ] **Step 3: Smoke-test again**

Run: `./dev.sh`

In the browser, reload (Streamlit's runOnSave should pick up the CSS via `ui/styles.py::inject_css` which is mtime-keyed). Verify:
1. The new section visually matches the rest of the result UI (no clashing colors, no broken layout).
2. Rows stack cleanly on narrow widths (resize the window).
3. The Details disclosure inside each row uses the same chevron/footer look as winner/alternative (rendered by the existing `_render_details` via `_render_efficiency_row`).

Stop the dev server when done.

---

## Final Verification

- [ ] **Run the full test suite once more**

Run: `uv run --with pytest pytest tests/ -v`
Expected: All tests pass, including the new `TestDayPlanMetrics` (4 cases) and `TestEfficiencyOptions` (6 cases).

- [ ] **Verify no regression in the main scenario from the brainstorm**

In the browser, reproduce the 2026-06-01 Prenzlau scenario from the screenshots:
- Brandenburg + Mecklenburg-Vorpommern, 38 tours.
- Winner: Tour 722586 Hoyerswerda → Leipzig, 44,02 €, 17h59 day, ≈ 2,45 €/h.
- "Weitere Optionen" should now surface Tour 721174 (Stralsund → Angermünde, 42,12 €) with substantially lower overhead and a better €/h.

- [ ] **Hand back to the user**

Report what changed, list the modified files, and explicitly **do not** commit. The user reviews the working tree and runs `git commit` themselves.
