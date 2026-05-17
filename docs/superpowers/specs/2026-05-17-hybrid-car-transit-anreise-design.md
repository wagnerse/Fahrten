# Hybrid Car+Transit Anreise — Design Spec

Date: 2026-05-17
Author: brainstormed with user (Sebastian + Papa)

## Problem

Tours whose start station is not directly reachable by transit from the home
station (within the user's earliest-departure window) are filtered out in
`_filter_and_sort_reachable` (`optimizer.py:122`). The user has identified
that some of these tours **would** be reachable if the user drove their car
to a station closer to the tour-start and continued by train from there.

Concrete example, 2026-06-01 from Prenzlau: Tour 721174 (Stralsund Hbf 06:12
→ Angermünde 08:25, 42.12 €) is not reachable by transit because the first
train from Prenzlau to Stralsund arrives too late. But it is reachable as:

1. 03:30 car Prenzlau → Pasewalk (~30 min, within the existing 50-min
   driving-radius constraint).
2. ~04:16 RE3 Pasewalk → Stralsund Hbf (~1 h).
3. 06:12 tour Stralsund Hbf → Angermünde.
4. RE3 Angermünde → Pasewalk (~30 min) — the existing relaxation handles
   this already.
5. Car Pasewalk → Prenzlau (~30 min).

The current car-mode optimizer (`optimize_day_car_mode`) requires the tour
to **start at the park-station**, so it cannot consider this combination.

## Goal

Extend car-mode so the seed step also admits tours whose start station is
reachable by transit from the park-station within the time budget, but only
when those tours were not already reachable by direct transit from home.

## Non-goals

- No new UI toggle. Hybrid runs automatically when car-mode is active
  (`max_car_minutes > 0` and home equals destination).
- No asymmetric park-stations. Outbound and return use the same park-station.
- No new `ChainLink` type. We chain existing types longer: `car_outbound`
  followed by `outbound` (transit) followed by `tour`.
- No new optimizer function. We extend `_build_car_chain_for_candidate`.
- No tooltip / UI explanation of why a tour is hybrid — the chain rendering
  speaks for itself.

## Algorithm

`_build_car_chain_for_candidate` (`optimizer.py:543`) currently seeds the DP
only with tours that start at the park-station candidate:

```python
for i, tour in enumerate(tours):
    if stations_match(tour.departure_station, candidate) and tour.departure_dt >= car_arrival:
        dp[i] = tour.euros
```

The extension adds a second seeding branch for tours where:

1. The tour's start station is **not** the candidate (otherwise the direct
   seed already handled it).
2. The tour was **not** directly reachable by transit from home (otherwise
   the plain transit pass already handled it — no benefit from going hybrid).
3. There exists a valid transit connection from the candidate to the tour's
   start station, departing no earlier than `car_arrival` and arriving no
   later than `tour.departure_dt − MIN_TRANSFER_MINUTES`.

Pseudocode (additions only — the direct seed loop above is unchanged):

```python
hybrid_anreise: dict[int, Connection] = {}  # tour-index → outbound transit conn

for i, tour in enumerate(tours):
    if stations_match(tour.departure_station, candidate):
        continue  # direct seed already covers this
    if tour.tour_nr in directly_reachable_tour_nrs:
        continue  # plain transit Anreise was already feasible — no benefit
    must_arrive_by = tour.departure_dt - timedelta(minutes=MIN_TRANSFER_MINUTES)
    start_id = get_id(tour.departure_station)
    if not start_id or not cand_id:
        continue
    transit_conn = check_reachability_with_ids(
        cand_id, start_id, car_arrival, must_arrive_by,
    )
    if transit_conn is None:
        continue
    dp[i] = tour.euros
    hybrid_anreise[i] = transit_conn
```

The DAG-DP itself (`for j in range(n): for i in range(j): ...`) does not
change. Chains can now start at any tour i that has either a direct seed or
a hybrid seed; both contribute identically to the DP.

The chain-build step inserts the hybrid outbound transit link **after the
`car_outbound` link and before the first tour**, only when the chain's first
tour-index is in `hybrid_anreise`:

```python
plan.chain.append(ChainLink(type="car_outbound", car_leg=...))

first_idx = chain_indices[0]
if first_idx in hybrid_anreise:
    conn = hybrid_anreise[first_idx]
    plan.chain.append(ChainLink(
        type="outbound",
        connection=conn,
        warning="Schienenersatzverkehr auf der Anreise!"
            if conn.has_replacement_service else None,
    ))

# existing: tour links, transfers, optional inbound transit, car_inbound
```

The Rückreise side already supports the symmetric case (transit from
tour-end back to the park-station — `optimizer.py:600-605`). No change there.

## Architecture

### `fahrtenplaner/optimizer.py`

**`optimize_day`** — extended to also return the set of `tour_nr`s for which
a direct transit Anreise from home was feasible:

```python
def optimize_day(
    tours, home_station, dest_station, earliest_departure, latest_return,
    progress_callback=None, max_transfer_gap_hours=MAX_TRANSFER_GAP_HOURS,
) -> tuple[DayPlan, list[DayPlan], set[int]]:
    ...
    # Phase 1
    outbound, api1 = _compute_outbound(tours, ...)
    # NEW: snapshot reachability by tour_nr BEFORE the filter+sort step
    # mutates `tours` and `outbound`. Keying by tour_nr (not list index)
    # means the set survives the reordering.
    directly_reachable = {
        t.tour_nr for t, conn in zip(tours, outbound) if conn is not None
    }
    tours, outbound = _filter_and_sort_reachable(tours, outbound)
    ...
    return plan, candidates, directly_reachable
```

The snapshot must happen **between** `_compute_outbound` and
`_filter_and_sort_reachable` so it sees every tour, including the ones
about to be dropped. Keying by `tour_nr` (a stable identifier on `Tour`)
rather than list index means the set stays correct after the subsequent
filter+sort.

This is a breaking signature change that requires updating the same
callsites Wave 2 of the previous feature touched: the `run_optimizer` helper
in `tests/test_optimizer.py`, the `optimize_with_modes` call site, and any
`patch(..., return_value=...)` mocks of `optimize_day`.

**`optimize_day_car_mode`** — new keyword-only parameter
`directly_reachable_tour_nrs: set[int] = frozenset()`:

```python
def optimize_day_car_mode(
    tours, home_station, earliest_departure, latest_return,
    max_car_minutes, fuel_consumption, fuel_price,
    *,
    directly_reachable_tour_nrs: set[int] = frozenset(),
    progress_callback=None, max_transfer_gap_hours=MAX_TRANSFER_GAP_HOURS,
) -> tuple[DayPlan, list[DayPlan]]:
```

Default `frozenset()` makes the parameter backwards-compatible: callers that
don't pass it get the previous behaviour (no hybrid pass). The keyword-only
marker (`*`) prevents accidental positional passing of an unrelated value.

The set is forwarded to `_build_car_chain_for_candidate` which uses it to
skip tours already covered by direct transit.

**`_build_car_chain_for_candidate`** — extended seed loop and chain build as
described in the Algorithm section.

**`optimize_with_modes`** — extracts the set from `optimize_day`'s output
and forwards it to `optimize_day_car_mode`:

```python
transit_plan, transit_candidates, directly_reachable = optimize_day(...)
...
if max_car_minutes > 0 and stations_match(home_station, dest_station):
    car_plan, car_candidates = optimize_day_car_mode(
        ...,
        directly_reachable_tour_nrs=directly_reachable,
    )
```

**New constant** near the existing `EFFICIENCY_*` block:

```python
HYBRID_MAX_API_LOOKUPS_PER_CANDIDATE: int = 20
```

Safety net. If after pruning more than 20 lookups would still be needed for
a single park-station, candidates are sorted by tour `euros` descending and
the top 20 are kept; the rest are reported in the progress log as "Hybrid-
Suche begrenzt: N Touren übersprungen".

### `fahrtenplaner/ui/`

**No changes.** The longer chain (`car_outbound` → `outbound` → `tour` →
... → `car_inbound`) walks through the existing `_chain_segments` renderer
(`render.py:137`) without special handling. The bahn-row timeline shows
distinct segments for car-leg and outbound transit. The detail disclosure
walks the chain and renders each link via the existing dispatch in
`_render_details` (`render.py:470-481`).

### Tests

`tests/test_optimizer.py` gets two new test classes.

`TestHybridAnreise` — four unit tests that exercise the seed loop directly
by calling `optimize_day_car_mode` with mocked `check_reachability_with_ids`
and `driving_info`:

- `test_hybrid_seed_used_when_direct_anreise_failed` — a tour starting at a
  non-candidate station; direct transit from home unreachable; transit from
  candidate to tour-start feasible → tour appears in the result chain with
  an `outbound` link after `car_outbound`.
- `test_hybrid_skipped_when_direct_anreise_succeeded` — the same tour, but
  with `directly_reachable_tour_nrs={tour.tour_nr}` passed in → the hybrid
  seed branch never runs (verified by asserting
  `check_reachability_with_ids` is not called for the candidate→tour-start
  pair).
- `test_hybrid_skipped_when_no_transit_from_park` — direct unreachable AND
  transit from park unreachable → tour is not in the chain.
- `test_hybrid_skipped_when_transit_too_late` — direct unreachable; transit
  from park exists but arrives after `tour.departure_dt -
  MIN_TRANSFER_MINUTES` → not seeded.

`TestOptimizeWithModesHybrid` — two integration tests with all three
relevant clients mocked at the `optimizer` module level:

- `test_directly_reachable_set_is_forwarded_to_car_mode` — verifies the
  `set[int]` returned from `optimize_day` is passed as
  `directly_reachable_tour_nrs` to `optimize_day_car_mode`. Done with
  `patch(...)` and `assert_called_with` (kwargs inspection).
- `test_end_to_end_pasewalk_scenario` — mimics the 721174 case: home
  Prenzlau, direct transit to Stralsund Hbf returns None, driving to
  Pasewalk returns (30, 32.0), transit Pasewalk→Stralsund Hbf returns a
  Connection, tour 721174 ends at Angermünde, transit Angermünde→Pasewalk
  returns a Connection. Assert the winner is a hybrid plan: chain types are
  `["car_outbound", "outbound", "tour", "inbound", "car_inbound"]`.

`TestCarMode` — two existing tests need signature updates:

- `test_finds_chain_starting_and_ending_at_same_station` and
  `test_finds_chain_with_transit_return_to_car`: their direct calls to
  `optimize_day_car_mode` continue to work because the new param is
  keyword-only with a safe default. **No change needed.**
- `test_winner_is_decided_by_net_euros` and
  `test_car_mode_skipped_when_dest_differs`: the mocks of `optimize_day`
  return a 2-tuple (`(plan, candidates)`). After this change they must
  return a 3-tuple (`(plan, candidates, set())`).

`TestEfficiencyOptions.test_optimize_with_modes_populates_efficiency_options`
(from the previous feature) — same fix: mock return becomes 3-tuple.

## Data flow

```
optimize_with_modes
├── optimize_day                       → (winner, candidates, directly_reachable)
│       directly_reachable = {tour_nr: outbound_was_feasible}
├── optimize_day_car_mode (optional, with directly_reachable_tour_nrs)
│   for each park-station candidate:
│     _build_car_chain_for_candidate(...)
│       seed direct (existing) ────── tours starting AT candidate
│       seed hybrid (NEW) ─────────── tours where:
│                                       - start ≠ candidate
│                                       - tour_nr ∉ directly_reachable
│                                       - transit candidate→start feasible
│       DP runs over both seed types (unchanged)
│       chain build inserts outbound transit link when first_idx ∈ hybrid_anreise
│   → (best_car_plan, car_candidates)
└── _build_efficiency_options(...)  (unchanged)
```

## Edge cases

- **All tours directly reachable.** `directly_reachable_tour_nrs` contains
  every tour; the hybrid seed branch skips every tour; behaviour identical
  to today's car-mode.
- **No park-stations within drive radius.** Existing guard:
  `optimize_day_car_mode` short-circuits, returns `(DayPlan(), [])`. Hybrid
  doesn't even start.
- **Candidate ID lookup fails for some park-station.** Existing guard skips
  that candidate; hybrid skips with it.
- **Tour starts at home.** Direct transit Anreise is trivially feasible
  (empty connection); `directly_reachable_tour_nrs` includes the tour;
  hybrid never runs for it. No issue.
- **The same chain is reachable both directly (car_outbound → tour at
  candidate) and via hybrid (car_outbound → outbound → tour at non-candidate
  reachable from candidate).** Different candidates produce different
  chains. Both are surfaced via `_enumerate_chain_candidates` in the
  existing efficiency_options flow. Dedupe still works because
  `_plan_identity` keys on `(tuple of tour_nrs, has_car_legs)` — both have
  car legs but the tour-tuple differs unless they happen to cover the same
  tours, in which case dedupe correctly collapses them.
- **More than HYBRID_MAX_API_LOOKUPS_PER_CANDIDATE tours qualify for one
  candidate after pruning.** Cap at the top-K by `tour.euros` desc, log a
  caption "Hybrid-Suche begrenzt: N Touren übersprungen". Surfacing
  guarantees the user knows it happened.

## Error handling

No new error paths. The added logic uses existing primitives
(`check_reachability_with_ids`, `get_id`, `stations_match`) and existing
return-None semantics.

## File checklist

- `fahrtenplaner/optimizer.py`
  - Add constant `HYBRID_MAX_API_LOOKUPS_PER_CANDIDATE = 20`.
  - Change `optimize_day` return to `tuple[DayPlan, list[DayPlan], set[int]]`
    (breaking signature change).
  - Change `optimize_day_car_mode` to accept keyword-only
    `directly_reachable_tour_nrs: set[int] = frozenset()`.
  - Extend `_build_car_chain_for_candidate` with hybrid seeding and chain
    build for the new `outbound` link (the function gains a
    `directly_reachable_tour_nrs` parameter, threaded from car-mode).
  - Update `optimize_with_modes` to capture the set and forward it.
- `tests/test_optimizer.py`
  - Update `run_optimizer` helper to unpack 3-tuple from `optimize_day`.
  - Update 3 `patch(...)` mocks to return 3-tuples instead of 2-tuples
    (`test_winner_is_decided_by_net_euros`,
    `test_car_mode_skipped_when_dest_differs`,
    `TestEfficiencyOptions.test_optimize_with_modes_populates_efficiency_options`).
  - Add `TestHybridAnreise` (4 cases) and `TestOptimizeWithModesHybrid`
    (2 cases).

All code comments in English. UI strings stay in German.
