# Hybrid Car+Transit Anreise Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **⛔ HARD RULE (project-specific, overrides skill defaults):** Do **NOT** run `git add`, `git commit`, or `git push` at any point during execution. The CLAUDE.md hard rule applies: only the human user authorizes git history changes, and only with the literal word "commit" or "push". Leave every task's changes as uncommitted working-tree modifications. Subagent prompts must explicitly include "do not commit".

**Goal:** Let car-mode also handle tours that don't start at the park-station by adding a transit leg from the park-station to the tour-start, but only when direct transit from home would have failed.

**Architecture:** Extend `optimize_day_car_mode` / `_build_car_chain_for_candidate` with a second seeding branch. `optimize_day` exposes which tours were directly reachable so the hybrid pass can skip those (no benefit). A new `outbound` ChainLink is inserted after `car_outbound` when the chain's first tour was hybrid-seeded.

**Tech Stack:** Python 3.12, Streamlit, pytest, `uv`. No new dependencies. UI rendering is unchanged — the existing chain renderer handles the longer chain shape.

**Spec:** `docs/superpowers/specs/2026-05-17-hybrid-car-transit-anreise-design.md`

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `fahrtenplaner/optimizer.py` | modify | Constant; signature change on `optimize_day` and `optimize_day_car_mode`; hybrid seed + chain build in `_build_car_chain_for_candidate`; wiring in `optimize_with_modes`. |
| `tests/test_optimizer.py` | modify | Update `run_optimizer` helper + 3 patch-mocks to unpack/return 3-tuples. Add `TestHybridAnreise` and `TestOptimizeWithModesHybrid`. |

No UI changes. No new files.

---

## Task 1: Change `optimize_day` to return `(DayPlan, list[DayPlan], set[int])`

**Files:**
- Modify: `fahrtenplaner/optimizer.py`
- Modify: `tests/test_optimizer.py`

Breaking signature change. Snapshot the directly-reachable tour_nrs **between** `_compute_outbound` and `_filter_and_sort_reachable`, so the snapshot sees tours that are about to be dropped. Update all direct call sites in the same task to keep tests green.

- [ ] **Step 1: Locate the snapshot site**

Open `fahrtenplaner/optimizer.py` and find the `optimize_day` function (~line 458). Inside it, locate this block (it sits between Phase 1 and the filter step):

```python
    # ----- Phase 1: Anreise --------------------------------------------------
    outbound, api1 = _compute_outbound(
        tours, home_station, home_id, get_id, earliest_departure, report,
    )
    tours, outbound = _filter_and_sort_reachable(tours, outbound)
```

- [ ] **Step 2: Add the snapshot and change the function signature**

Change the function signature line:

```python
def optimize_day(
    tours: list[Tour],
    home_station: str,
    dest_station: str,
    earliest_departure: datetime,
    latest_return: datetime,
    progress_callback: ProgressCb = None,
    max_transfer_gap_hours: float = MAX_TRANSFER_GAP_HOURS,
) -> tuple[DayPlan, list[DayPlan], set[int]]:
```

Insert the snapshot between `_compute_outbound` and `_filter_and_sort_reachable`:

```python
    # ----- Phase 1: Anreise --------------------------------------------------
    outbound, api1 = _compute_outbound(
        tours, home_station, home_id, get_id, earliest_departure, report,
    )
    # Snapshot which tours are directly reachable by transit from home,
    # keyed by tour_nr (stable across the subsequent filter+sort). The
    # hybrid car-mode pass uses this to skip tours that don't benefit
    # from going via a park-station.
    directly_reachable: set[int] = {
        t.tour_nr for t, conn in zip(tours, outbound) if conn is not None
    }
    tours, outbound = _filter_and_sort_reachable(tours, outbound)
```

- [ ] **Step 3: Update every return statement in `optimize_day` to a 3-tuple**

The function has several early returns plus one success path. Each must now return three elements. Find and update each one:

```python
    if not tours:
        return DayPlan(), [], set()
```
```python
    if not home_id:
        report(1.0, f"Station '{home_station}' nicht gefunden!")
        return DayPlan(), [], set()
    if not dest_id:
        report(1.0, f"Station '{dest_station}' nicht gefunden!")
        return DayPlan(), [], set()
```

(Note: the `if not home_id` / `if not dest_id` branches sit **before** the snapshot, so `directly_reachable` doesn't exist yet — returning `set()` is correct.)

```python
    tours, outbound = _filter_and_sort_reachable(tours, outbound)
    if not tours:
        report(1.0, "Keine Tour von zuhause erreichbar!")
        return DayPlan(), [], directly_reachable
```

(After this branch, `directly_reachable` may be a populated set from the snapshot — but if the filter dropped every tour, every entry in `directly_reachable` came from `outbound[i] is not None` evaluated **before** the filter ran. Wait — if `outbound[i] is not None` was the very condition `_filter_and_sort_reachable` used to keep tours, the filter only drops tours with `outbound[i] is None`. So `directly_reachable` after the snapshot equals the set of `tour_nr`s for tours that survive the filter. If the filter wipes out everything, that means the snapshot is the empty set too. Returning `directly_reachable` is fine and correct.)

```python
    best_j = _find_best_chain_end(dp, inbound)
    if best_j == -1:
        report(1.0, "Keine gültige Tourenkette gefunden.")
        return DayPlan(), candidates, directly_reachable
```

```python
    return plan, candidates, directly_reachable
```

- [ ] **Step 4: Update the `run_optimizer` helper in tests**

In `tests/test_optimizer.py`, find the `run_optimizer` helper (~line 158). It currently looks like:

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

Change to:

```python
        from optimizer import optimize_day
        plan, _candidates, _directly_reachable = optimize_day(
            tours=tours,
            home_station=home,
            dest_station=dest,
            earliest_departure=datetime.combine(DAY, time(h1, m1)),
            latest_return=datetime.combine(DAY, time(h2, m2)),
            max_transfer_gap_hours=max_gap_hours,
        )
        return plan
```

- [ ] **Step 5: Update the three patch-mocks**

`patch("optimizer.optimize_day", return_value=...)` callers must return 3-tuples now. Find and update each:

In `test_winner_is_decided_by_net_euros` (~line 784), change:

```python
        with patch("optimizer.optimize_day", return_value=(transit_plan, [])), \
             patch("optimizer.optimize_day_car_mode", return_value=(car_plan, [])):
```

to:

```python
        with patch("optimizer.optimize_day", return_value=(transit_plan, [], set())), \
             patch("optimizer.optimize_day_car_mode", return_value=(car_plan, [])):
```

In `test_car_mode_skipped_when_dest_differs` (~line 805), change:

```python
        with patch("optimizer.optimize_day", return_value=(DayPlan(), [])) as transit_mock, \
             patch("optimizer.optimize_day_car_mode") as car_mock:
```

to:

```python
        with patch("optimizer.optimize_day", return_value=(DayPlan(), [], set())) as transit_mock, \
             patch("optimizer.optimize_day_car_mode") as car_mock:
```

In `test_optimize_with_modes_populates_efficiency_options` (inside `TestEfficiencyOptions`), find:

```python
        with patch("optimizer.optimize_day",
                   return_value=(winner_plan, [winner_plan, cheap_plan, mid_plan])), \
             patch("optimizer.stations_match", return_value=False):
```

Change to:

```python
        with patch("optimizer.optimize_day",
                   return_value=(winner_plan, [winner_plan, cheap_plan, mid_plan], set())), \
             patch("optimizer.stations_match", return_value=False):
```

- [ ] **Step 6: Update the call site in `optimize_with_modes`**

In `fahrtenplaner/optimizer.py`, find `optimize_with_modes`. The first line of the function body currently calls `optimize_day` like this:

```python
    transit_plan, transit_candidates = optimize_day(
        tours, home_station, dest_station,
        earliest_departure, latest_return,
        progress_callback=progress_callback,
        max_transfer_gap_hours=max_transfer_gap_hours,
    )
```

Change to:

```python
    transit_plan, transit_candidates, directly_reachable = optimize_day(
        tours, home_station, dest_station,
        earliest_departure, latest_return,
        progress_callback=progress_callback,
        max_transfer_gap_hours=max_transfer_gap_hours,
    )
```

(`directly_reachable` is captured but not used yet — Task 3 wires it through.)

- [ ] **Step 7: Run the full test suite**

Run: `uv run --with pytest pytest tests/ -q`
Expected: 81 passed (the same as the baseline before this task).

---

## Task 2: Add `directly_reachable_tour_nrs` parameter to `optimize_day_car_mode`

**Files:**
- Modify: `fahrtenplaner/optimizer.py`

Pure plumbing — accept the parameter, forward it to `_build_car_chain_for_candidate`. No behaviour change yet (we use a default of `frozenset()`).

- [ ] **Step 1: Add the keyword-only parameter to `optimize_day_car_mode`**

In `fahrtenplaner/optimizer.py`, find `optimize_day_car_mode` (~line 470). Change its signature:

```python
def optimize_day_car_mode(
    tours: list[Tour],
    home_station: str,
    earliest_departure: datetime,
    latest_return: datetime,
    max_car_minutes: int,
    fuel_consumption: float,
    fuel_price: float,
    *,
    directly_reachable_tour_nrs: set[int] = frozenset(),
    progress_callback: ProgressCb = None,
    max_transfer_gap_hours: float = MAX_TRANSFER_GAP_HOURS,
) -> tuple[DayPlan, list[DayPlan]]:
```

The `*` before `directly_reachable_tour_nrs` makes it (and the existing trailing args) keyword-only. `progress_callback` and `max_transfer_gap_hours` were already passed by keyword in every call site, so this is non-breaking.

- [ ] **Step 2: Forward to `_build_car_chain_for_candidate`**

Inside `optimize_day_car_mode`, find the inner loop that calls `_build_car_chain_for_candidate`:

```python
        plan = _build_car_chain_for_candidate(
            sorted_tours, edge, candidate, drive_min, drive_km, cost_per_km,
            earliest_departure, latest_return_hard, home_station, get_id,
        )
```

Change to:

```python
        plan = _build_car_chain_for_candidate(
            sorted_tours, edge, candidate, drive_min, drive_km, cost_per_km,
            earliest_departure, latest_return_hard, home_station, get_id,
            directly_reachable_tour_nrs=directly_reachable_tour_nrs,
        )
```

- [ ] **Step 3: Accept the parameter in `_build_car_chain_for_candidate`**

Find `_build_car_chain_for_candidate` (~line 543). Add the keyword-only parameter:

```python
def _build_car_chain_for_candidate(
    tours: list[Tour],
    edge: list[list[Optional[Connection]]],
    candidate: str,
    drive_min: int,
    drive_km: float,
    cost_per_km: float,
    earliest_departure: datetime,
    latest_return_hard: datetime,
    home_station: str,
    get_id: Callable[[str], Optional[str]],
    *,
    directly_reachable_tour_nrs: set[int] = frozenset(),
) -> DayPlan:
```

The parameter is unused inside the function body for now; the hybrid logic comes in Task 5. The default ensures the existing tests that don't pass the kwarg keep working.

- [ ] **Step 4: Run the full test suite**

Run: `uv run --with pytest pytest tests/ -q`
Expected: 81 passed. No behaviour change yet.

---

## Task 3: Wire `directly_reachable` through `optimize_with_modes`

**Files:**
- Modify: `fahrtenplaner/optimizer.py`

- [ ] **Step 1: Pass the set into the car-mode call**

In `fahrtenplaner/optimizer.py::optimize_with_modes`, find the existing car-mode call:

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

Change to:

```python
    car_plan: DayPlan = DayPlan()
    car_candidates: list[DayPlan] = []
    if max_car_minutes > 0 and stations_match(home_station, dest_station):
        car_plan, car_candidates = optimize_day_car_mode(
            tours, home_station,
            earliest_departure, latest_return,
            max_car_minutes, fuel_consumption, fuel_price,
            directly_reachable_tour_nrs=directly_reachable,
            progress_callback=progress_callback,
            max_transfer_gap_hours=max_transfer_gap_hours,
        )
```

- [ ] **Step 2: Run the full test suite**

Run: `uv run --with pytest pytest tests/ -q`
Expected: 81 passed.

---

## Task 4: Add the `HYBRID_MAX_API_LOOKUPS_PER_CANDIDATE` constant

**Files:**
- Modify: `fahrtenplaner/optimizer.py`

- [ ] **Step 1: Add the constant**

In `fahrtenplaner/optimizer.py`, find the existing `EFFICIENCY_*` constants block. Add right after it:

```python
# Safety cap for the hybrid car+transit Anreise pass. Per park-station
# candidate, at most this many transit lookups (candidate → tour-start)
# are made. Tours are sorted by revenue desc so the most valuable ones
# survive the cap. In practice the skip-direct-reachable prune brings
# the per-candidate count well below 20; this is insurance against
# pathological days with many high-value remote tours.
HYBRID_MAX_API_LOOKUPS_PER_CANDIDATE: int = 20
```

- [ ] **Step 2: Verify the module still imports**

Run: `uv run python -c "import sys; sys.path.insert(0, 'fahrtenplaner'); import optimizer; print(optimizer.HYBRID_MAX_API_LOOKUPS_PER_CANDIDATE)"`
Expected: prints `20`.

---

## Task 5: TDD — implement hybrid seed + chain build

**Files:**
- Modify: `fahrtenplaner/optimizer.py`
- Modify: `tests/test_optimizer.py`

This is the core task. Tests drive the implementation.

- [ ] **Step 1: Write the four failing tests**

Append to `tests/test_optimizer.py`:

```python
# ---------------------------------------------------------------------------
# Hybrid car+transit Anreise
# ---------------------------------------------------------------------------

class TestHybridAnreise:
    """Hybrid Anreise = car to a park-station + transit on to a tour-start.

    Setup pattern shared by all tests:
    - `tour` is the candidate hybrid tour: starts at Stralsund Hbf (≠ Pasewalk),
      arrives at Angermünde. Worth 42.12 €.
    - `decoy` is a do-nothing tour starting at Pasewalk at 03:00, BEFORE the
      car arrives at the park (04:00). Its only purpose is to put "Pasewalk"
      into `optimize_day_car_mode`'s candidate-set, which is derived from
      `t.departure_station`. Because decoy.departure_dt < car_arrival, the
      direct seed loop skips it, so it never contributes to the DP.
    - `_setup_geocode` returns stable IDs for the relevant stations.
    """

    DAY_ISO = "2026-04-01"

    def _setup_geocode(self):
        return {
            "Prenzlau":      {"id": "ChIJ_prenzlau",  "name": "Prenzlau"},
            "Pasewalk":      {"id": "ChIJ_pasewalk",  "name": "Pasewalk"},
            "Stralsund Hbf": {"id": "ChIJ_stralsund", "name": "Stralsund Hbf"},
            "Angermünde":    {"id": "ChIJ_angerm",   "name": "Angermünde"},
        }

    def _decoy(self):
        """Tour at Pasewalk that starts before car_arrival → puts Pasewalk
        into the candidate-set but is never seeded by the direct loop."""
        return make_tour(999000, "03:00", "Pasewalk", "03:30", "Pasewalk", 5.0)

    def test_hybrid_seed_used_when_direct_anreise_failed(self):
        """Tour 721174 starts at Stralsund. Pasewalk→Stralsund is reachable
        by transit; Prenzlau→Stralsund directly is not. The optimizer should
        build a hybrid chain car→Pasewalk, train→Stralsund, tour, train→Pasewalk, car→home."""
        from optimizer import optimize_day_car_mode

        tour = make_tour(721174, "06:12", "Stralsund Hbf", "08:25", "Angermünde", 42.12)
        decoy = self._decoy()

        anreise_transit = make_leg_conn(
            "Pasewalk", f"{self.DAY_ISO}T04:16:00",
            "Stralsund Hbf", f"{self.DAY_ISO}T05:30:00",
            line="RE3",
        )
        return_transit = make_leg_conn(
            "Angermünde", f"{self.DAY_ISO}T08:30:00",
            "Pasewalk", f"{self.DAY_ISO}T09:00:00",
            line="RE3",
        )

        def reachability_router(from_id, to_id, earliest_dep, must_arrive):
            if from_id == "ChIJ_pasewalk" and to_id == "ChIJ_stralsund":
                return anreise_transit
            if from_id == "ChIJ_angerm" and to_id == "ChIJ_pasewalk":
                return return_transit
            return None

        with patch("optimizer.batch_lookup_stations",
                   return_value=self._setup_geocode()), \
             patch("optimizer.driving_info", return_value=(30, 32.0)), \
             patch("optimizer.check_reachability_with_ids",
                   side_effect=reachability_router):
            plan, _candidates = optimize_day_car_mode(
                tours=[tour, decoy],
                home_station="Prenzlau",
                earliest_departure=datetime.combine(DAY, time(3, 30)),
                latest_return=datetime.combine(DAY, time(23, 59)),
                max_car_minutes=60,
                fuel_consumption=7.0,
                fuel_price=1.79,
                directly_reachable_tour_nrs=set(),
            )

        assert plan.num_tours == 1
        assert plan.tours[0].tour_nr == 721174
        types = [link.type for link in plan.chain]
        assert types == ["car_outbound", "outbound", "tour", "inbound", "car_inbound"]
        assert plan.chain[1].connection is anreise_transit  # the hybrid Anreise leg

    def test_hybrid_lookup_skipped_when_tour_directly_reachable(self):
        """When 721174 is in directly_reachable_tour_nrs, the optimizer must
        NOT make the Pasewalk→Stralsund reachability call. We assert on the
        call log, which makes this test independent of plan emptiness."""
        from optimizer import optimize_day_car_mode

        tour = make_tour(721174, "06:12", "Stralsund Hbf", "08:25", "Angermünde", 42.12)
        decoy = self._decoy()

        reachability_calls: list[tuple] = []

        def reachability_router(from_id, to_id, earliest_dep, must_arrive):
            reachability_calls.append((from_id, to_id))
            return None  # nothing reachable in either direction

        with patch("optimizer.batch_lookup_stations",
                   return_value=self._setup_geocode()), \
             patch("optimizer.driving_info", return_value=(30, 32.0)), \
             patch("optimizer.check_reachability_with_ids",
                   side_effect=reachability_router):
            optimize_day_car_mode(
                tours=[tour, decoy],
                home_station="Prenzlau",
                earliest_departure=datetime.combine(DAY, time(3, 30)),
                latest_return=datetime.combine(DAY, time(23, 59)),
                max_car_minutes=60,
                fuel_consumption=7.0,
                fuel_price=1.79,
                directly_reachable_tour_nrs={721174},
            )

        # The hybrid lookup Pasewalk → Stralsund Hbf must NEVER be called.
        # (Other lookups — e.g. transit-back-to-park for other candidates —
        # are unrelated and we don't constrain them here.)
        assert ("ChIJ_pasewalk", "ChIJ_stralsund") not in reachability_calls

    def test_hybrid_seed_skipped_when_no_transit_from_park(self):
        """Direct Anreise failed AND transit park→tour-start unreachable →
        the Stralsund tour does NOT appear in the result chain. Decoy tour
        may or may not appear — we only assert about 721174."""
        from optimizer import optimize_day_car_mode

        tour = make_tour(721174, "06:12", "Stralsund Hbf", "08:25", "Angermünde", 42.12)
        decoy = self._decoy()

        with patch("optimizer.batch_lookup_stations",
                   return_value=self._setup_geocode()), \
             patch("optimizer.driving_info", return_value=(30, 32.0)), \
             patch("optimizer.check_reachability_with_ids", return_value=None):
            plan, _candidates = optimize_day_car_mode(
                tours=[tour, decoy],
                home_station="Prenzlau",
                earliest_departure=datetime.combine(DAY, time(3, 30)),
                latest_return=datetime.combine(DAY, time(23, 59)),
                max_car_minutes=60,
                fuel_consumption=7.0,
                fuel_price=1.79,
                directly_reachable_tour_nrs=set(),
            )

        tour_nrs_in_chain = [t.tour_nr for t in plan.tours]
        assert 721174 not in tour_nrs_in_chain

    def test_hybrid_seed_skipped_when_transit_too_late(self):
        """Transit Pasewalk→Stralsund exists but arrives after the
        must-arrive-by deadline. The Stralsund tour is not seeded."""
        from optimizer import optimize_day_car_mode

        tour = make_tour(721174, "06:12", "Stralsund Hbf", "08:25", "Angermünde", 42.12)
        decoy = self._decoy()

        # In production, check_reachability_with_ids enforces the deadline
        # itself and returns None when no train arrives in time. We mimic
        # that by returning None whenever must_arrive_by is tighter than the
        # only available train (arrives 06:10, deadline 06:07).
        def reachability_router(from_id, to_id, earliest_dep, must_arrive):
            if from_id == "ChIJ_pasewalk" and to_id == "ChIJ_stralsund":
                # Only train available arrives 06:10 → too late for 06:07 deadline.
                return None
            return None

        with patch("optimizer.batch_lookup_stations",
                   return_value=self._setup_geocode()), \
             patch("optimizer.driving_info", return_value=(30, 32.0)), \
             patch("optimizer.check_reachability_with_ids",
                   side_effect=reachability_router):
            plan, _candidates = optimize_day_car_mode(
                tours=[tour, decoy],
                home_station="Prenzlau",
                earliest_departure=datetime.combine(DAY, time(3, 30)),
                latest_return=datetime.combine(DAY, time(23, 59)),
                max_car_minutes=60,
                fuel_consumption=7.0,
                fuel_price=1.79,
                directly_reachable_tour_nrs=set(),
            )

        tour_nrs_in_chain = [t.tour_nr for t in plan.tours]
        assert 721174 not in tour_nrs_in_chain
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --with pytest pytest tests/test_optimizer.py::TestHybridAnreise -v`
Expected: All 4 FAIL. The first one (`test_hybrid_seed_used_when_direct_anreise_failed`) fails with `assert plan.num_tours == 1` because the current seed loop only handles direct seeds and `Stralsund Hbf != Pasewalk`.

- [ ] **Step 3: Implement the hybrid seed loop and chain build**

In `fahrtenplaner/optimizer.py`, find `_build_car_chain_for_candidate` (~line 543). Locate the direct seed loop:

```python
    # Seed: tours that start at the candidate station after car arrival.
    for i, tour in enumerate(tours):
        if stations_match(tour.departure_station, candidate) and tour.departure_dt >= car_arrival:
            dp[i] = tour.euros
```

Replace with the direct seed plus a new hybrid seed block:

```python
    # Seed (direct): tours that start at the candidate station after car arrival.
    for i, tour in enumerate(tours):
        if stations_match(tour.departure_station, candidate) and tour.departure_dt >= car_arrival:
            dp[i] = tour.euros

    # Seed (hybrid): tours whose start station is reachable by transit from the
    # candidate park-station within the time budget. Only considered for tours
    # that were NOT directly reachable from home (no benefit otherwise) and
    # that do not start at the candidate (already handled above).
    #
    # Cap at HYBRID_MAX_API_LOOKUPS_PER_CANDIDATE highest-value tours so the
    # Maps API cost stays bounded on pathological days.
    hybrid_anreise: dict[int, Connection] = {}
    cand_id = get_id(candidate)
    eligible_for_hybrid = [
        (i, t) for i, t in enumerate(tours)
        if not stations_match(t.departure_station, candidate)
        and t.tour_nr not in directly_reachable_tour_nrs
        and t.departure_dt >= car_arrival + timedelta(minutes=MIN_TRANSFER_MINUTES)
    ]
    eligible_for_hybrid.sort(key=lambda pair: -pair[1].euros)
    for i, tour in eligible_for_hybrid[:HYBRID_MAX_API_LOOKUPS_PER_CANDIDATE]:
        start_id = get_id(tour.departure_station)
        if not start_id or not cand_id:
            continue
        must_arrive_by = tour.departure_dt - timedelta(minutes=MIN_TRANSFER_MINUTES)
        transit_conn = check_reachability_with_ids(
            cand_id, start_id, car_arrival, must_arrive_by,
        )
        if transit_conn is None:
            continue
        dp[i] = tour.euros
        hybrid_anreise[i] = transit_conn
```

Now find the chain-build block at the bottom of `_build_car_chain_for_candidate`. It currently starts with:

```python
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
```

Insert the hybrid outbound link between `car_outbound` and the first tour-link:

```python
    plan = DayPlan()
    plan.chain.append(ChainLink(
        type="car_outbound",
        car_leg=CarLeg(
            from_station=home_station, to_station=candidate,
            minutes=drive_min, km=drive_km, cost=leg_cost,
        ),
    ))
    # Hybrid Anreise: insert the transit leg from candidate → tour-start
    # before the first tour, when that tour was seeded via the hybrid pass.
    first_idx = chain_indices[0]
    if first_idx in hybrid_anreise:
        conn = hybrid_anreise[first_idx]
        warning = (
            "Schienenersatzverkehr auf der Anreise!"
            if conn.has_replacement_service else None
        )
        plan.chain.append(ChainLink(
            type="outbound", connection=conn, warning=warning,
        ))
    for pos, idx in enumerate(chain_indices):
        plan.chain.append(ChainLink(type="tour", tour=tours[idx]))
```

- [ ] **Step 4: Run the hybrid tests**

Run: `uv run --with pytest pytest tests/test_optimizer.py::TestHybridAnreise -v`
Expected: All 4 PASS.

- [ ] **Step 5: Run the full suite**

Run: `uv run --with pytest pytest tests/ -q`
Expected: 85 passed (81 baseline + 4 new).

---

## Task 6: Integration test — `TestOptimizeWithModesHybrid`

**Files:**
- Modify: `tests/test_optimizer.py`

End-to-end check that the new piece composes with `optimize_with_modes`.

- [ ] **Step 1: Write the integration tests**

Append to `tests/test_optimizer.py`:

```python
class TestOptimizeWithModesHybrid:
    """End-to-end: optimize_with_modes plumbs directly_reachable through to
    car-mode and the hybrid pass surfaces tours that direct transit missed.
    """

    def test_directly_reachable_set_is_forwarded_to_car_mode(self):
        """The set returned by optimize_day arrives at optimize_day_car_mode."""
        from optimizer import optimize_with_modes
        from models import DayPlan

        sentinel_set = {12345, 67890}

        with patch("optimizer.optimize_day",
                   return_value=(DayPlan(), [], sentinel_set)), \
             patch("optimizer.stations_match", return_value=True), \
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

        # Inspect the kwargs the car-mode mock was called with.
        car_mock.assert_called_once()
        kwargs = car_mock.call_args.kwargs
        assert kwargs["directly_reachable_tour_nrs"] == sentinel_set

    def test_end_to_end_pasewalk_scenario(self):
        """Tour starts at Stralsund (not reachable by direct transit from Prenzlau)
        but IS reachable via car-to-Pasewalk + RE3 Pasewalk→Stralsund. The
        winner is the hybrid plan.

        A `decoy` tour at Pasewalk (starts 03:00, before car arrival)
        gets Pasewalk into the candidate-set without affecting the DP."""
        from optimizer import optimize_with_modes

        tour = make_tour(721174, "06:12", "Stralsund Hbf", "08:25", "Angermünde", 42.12)
        decoy = make_tour(999000, "03:00", "Pasewalk", "03:30", "Pasewalk", 5.0)

        anreise_transit = make_leg_conn(
            "Pasewalk", "2026-04-01T04:16:00",
            "Stralsund Hbf", "2026-04-01T05:30:00",
            line="RE3",
        )
        return_transit = make_leg_conn(
            "Angermünde", "2026-04-01T08:30:00",
            "Pasewalk", "2026-04-01T09:00:00",
            line="RE3",
        )

        geocode = {
            "Prenzlau":      {"id": "ChIJ_prenzlau",  "name": "Prenzlau"},
            "Pasewalk":      {"id": "ChIJ_pasewalk",  "name": "Pasewalk"},
            "Stralsund Hbf": {"id": "ChIJ_stralsund", "name": "Stralsund Hbf"},
            "Angermünde":    {"id": "ChIJ_angerm",   "name": "Angermünde"},
        }

        def reachability_router(from_id, to_id, earliest_dep, must_arrive):
            # Direct Prenzlau → anything: unreachable (no early train Prenzlau→Stralsund).
            if from_id == "ChIJ_prenzlau":
                return None
            # Anything → Prenzlau: default unreachable for this test; only the
            # car-mode plan is meaningful and it doesn't need a Rückreise to Prenzlau
            # (the car drives back from the park-station).
            if to_id == "ChIJ_prenzlau":
                return None
            # Pasewalk → Stralsund: feasible (hybrid Anreise).
            if from_id == "ChIJ_pasewalk" and to_id == "ChIJ_stralsund":
                return anreise_transit
            # Angermünde → Pasewalk: feasible (transit-back-to-park).
            if from_id == "ChIJ_angerm" and to_id == "ChIJ_pasewalk":
                return return_transit
            return None

        with patch("optimizer.batch_lookup_stations", return_value=geocode), \
             patch("optimizer.driving_info", return_value=(30, 32.0)), \
             patch("optimizer.check_reachability_with_ids",
                   side_effect=reachability_router):
            result = optimize_with_modes(
                tours=[tour, decoy],
                home_station="Prenzlau", dest_station="Prenzlau",
                earliest_departure=datetime.combine(DAY, time(3, 30)),
                latest_return=datetime.combine(DAY, time(23, 59)),
                max_car_minutes=60,
                fuel_consumption=7.0,
                fuel_price=1.79,
            )

        # Transit-only plan is empty (no direct Anreise to Stralsund or Pasewalk).
        # Car-mode via Pasewalk hybrid wins, picking the high-value tour 721174.
        assert result.winner.num_tours == 1
        assert result.winner.has_car_legs
        assert result.winner.tours[0].tour_nr == 721174
        types = [link.type for link in result.winner.chain]
        assert types == ["car_outbound", "outbound", "tour", "inbound", "car_inbound"]
```

- [ ] **Step 2: Run the new tests**

Run: `uv run --with pytest pytest tests/test_optimizer.py::TestOptimizeWithModesHybrid -v`
Expected: 2 PASS.

- [ ] **Step 3: Run the full suite**

Run: `uv run --with pytest pytest tests/ -q`
Expected: 87 passed (81 baseline + 4 from Task 5 + 2 new).

---

## Final Verification

- [ ] **Manual smoke-test in the browser**

Run: `./dev.sh`

In the browser:
1. Pick 2026-06-01, Brandenburg + Mecklenburg-Vorpommern, "Touren laden".
2. Heimatbahnhof: Prenzlau, "Ankunft = Abfahrt" on, Auto-Anfahrt ≤ 50 min.
3. Earliest departure: 03:00 (give Pasewalk hybrid Anreise room to breathe).
4. Click "Optimale Route berechnen".

Verify:
- Tour 721174 Stralsund Hbf → Angermünde now appears either as the winner OR in the "Weitere Optionen — nach Aufwand sortiert" list.
- The chain visually shows: 🚗 Auto-Anfahrt Prenzlau → Pasewalk, 🚉 Anreise Pasewalk → Stralsund Hbf, Tour, 🚆 Rückfahrt zum Auto Angermünde → Pasewalk, 🚗 Auto-Rückfahrt Pasewalk → Prenzlau.
- Other previously-impossible tours starting at Stralsund (721173, 721234, 721172) may also surface.

Stop the dev server when done.

- [ ] **Hand back to the user**

Report what changed, list the modified files, do **not** commit. The user reviews the working tree and runs `git commit` themselves.
