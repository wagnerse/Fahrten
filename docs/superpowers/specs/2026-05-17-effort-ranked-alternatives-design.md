# Effort-ranked Alternatives — Design Spec

Date: 2026-05-17
Author: brainstormed with user (Sebastian + Papa)

## Problem

The current optimizer (`optimizer.py::optimize_day` + `optimize_with_modes`)
picks the chain that maximises gross revenue. That can return a plan with a
poor revenue-per-hour ratio — e.g. on 2026-06-01 from Prenzlau the winner is
Tour 722586 (Hoyerswerda → Leipzig, **44.02 €**) which forces a **4h23
Anreise + 2h19 Tour + 3h45 Rückreise**, total **17h59 door-to-door** ≈ **2.45
€/h**. A nearby alternative Tour 721174 (Stralsund → Angermünde, **42.12 €**)
yields a much shorter day and a higher hourly rate, but is invisible in the
current UI.

## Goal

Surface effort-aware alternatives **alongside** today's maximum-revenue
winner so the user can decide for themselves. The optimizer's core scoring
rule does **not** change.

## Non-goals

- No new optimisation objective. Max-€ remains the winner-selection rule.
- No Pareto front, no €/h slider, no list filters.
- No per-alternative feedback button. The feedback flow still targets the
  winner.
- No persistence of which alternative the user expanded (Streamlit-rerun
  semantics apply as today).

## Definitions

- **Aufwand (overhead time):** sum of all non-tour durations in the chain —
  Anreise + every Transfer connection + Rückreise + every car leg. The paid
  tour durations are excluded; they are not "effort cost" in the user's
  framing.
- **€/h:** `net_euros / overhead_hours`. Net (gross minus fuel) is used so
  the metric stays consistent with the existing winner-selection rule.

## Architecture

Three layers change. The optimizer stays framework-agnostic; the UI stays in
`ui/`. Backwards compatibility for `OptimizationResult.winner` and
`OptimizationResult.alternative` is preserved — the new field is additive.

### 1. `fahrtenplaner/models.py`

Two new properties on `DayPlan`:

```python
@property
def overhead_duration(self) -> timedelta:
    """Sum of all non-tour durations: connections (outbound, transfer,
    inbound) plus car legs. Paid tour time is excluded."""
    total = timedelta(0)
    for link in self.chain:
        if link.connection and link.connection.duration:
            total += link.connection.duration
        if link.car_leg:
            total += timedelta(minutes=link.car_leg.minutes)
    return total

@property
def euros_per_hour(self) -> float:
    """Net euros per hour of overhead. 0.0 if overhead is zero (degenerate)."""
    hours = self.overhead_duration.total_seconds() / 3600
    return self.net_euros / hours if hours > 0 else 0.0
```

Extension of `OptimizationResult`:

```python
@dataclass
class OptimizationResult:
    winner: DayPlan
    alternative: Optional[DayPlan] = None
    efficiency_options: list[DayPlan] = field(default_factory=list)  # NEW
    latest_return_target: Optional[datetime] = None
```

`has_alternative` stays as is.

### 2. `fahrtenplaner/optimizer.py`

Two new module-level constants:

```python
EFFICIENCY_TOP_K = 5            # how many effort-ranked alternatives to surface
EFFICIENCY_MIN_NET_EUROS = 10.0 # prune low-revenue micro-trips
```

New helper, parallel to the existing `_find_best_chain_end`:

```python
def _enumerate_chain_candidates(
    tours: list[Tour],
    outbound: list[Optional[Connection]],
    edge: list[list[Optional[Connection]]],
    inbound: list[Optional[Connection]],
    dp: list[float],
    pred: list[int],
) -> list[DayPlan]:
    """For each end-tour j with valid Rückreise, reconstruct its best chain.
    Returns one DayPlan per valid end-tour. The DP already encodes 'best
    chain ending at j', so this is a pure unpacking step — no extra search."""
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

`optimize_day` returns its candidate list alongside the chosen plan. Concrete
signature change:

```python
def optimize_day(...) -> tuple[DayPlan, list[DayPlan]]:
    ...
    plan = _build_dayplan(...)             # the winner (max revenue)
    candidates = _enumerate_chain_candidates(tours, outbound, edge, inbound, dp, pred)
    return plan, candidates
```

`optimize_day_car_mode` analogously: it already loops over candidate
park-stations and computes the best chain for each via
`_build_car_chain_for_candidate`. Today only the best of those is returned.
After the change, every per-park-station plan with at least one tour is
collected as a candidate. **N park-stations within driving radius → up to N
car-mode candidates**, one per park-station. `_build_car_chain_for_candidate`
itself does not change — only the caller loop keeps both `best_plan` and the
running list:

```python
def optimize_day_car_mode(...) -> tuple[DayPlan, list[DayPlan]]:
    ...
    candidates: list[DayPlan] = []
    for candidate in candidates_list:
        ...
        plan = _build_car_chain_for_candidate(...)
        if plan.num_tours > 0:
            candidates.append(plan)
            if plan.net_euros > best_plan.net_euros:
                best_plan = plan
    return best_plan, candidates
```

`optimize_with_modes` is the orchestrator that picks winner/alternative and
assembles the effort-ranked list:

```python
def optimize_with_modes(...) -> OptimizationResult:
    transit_winner, transit_candidates = optimize_day(...)
    car_winner, car_candidates = (DayPlan(), [])
    if max_car_minutes > 0 and stations_match(home_station, dest_station):
        car_winner, car_candidates = optimize_day_car_mode(...)

    plans = [p for p in (transit_winner, car_winner) if p.num_tours > 0]
    if not plans:
        return OptimizationResult(winner=DayPlan(), alternative=None,
                                  latest_return_target=latest_return)
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

`_build_efficiency_options` does the work:

```python
def _build_efficiency_options(
    candidates: list[DayPlan],
    excluded: list[DayPlan],
) -> list[DayPlan]:
    """Filter, dedupe, sort, truncate.
    1. Drop plans with net_euros < EFFICIENCY_MIN_NET_EUROS.
    2. Dedupe by (tuple of tour_nrs, has_car_legs) — keep first occurrence.
    3. Drop any plan whose identity matches a plan in `excluded`.
    4. Sort by overhead_duration ascending (tie-break: net_euros descending).
    5. Truncate to EFFICIENCY_TOP_K.
    """
```

The identity key for both dedup and exclusion: the **ordered tuple of
`tour.tour_nr`** in the chain plus the `has_car_legs` flag. This is enough
to distinguish "same tours via transit" from "same tours via car" while
collapsing accidental duplicates within one mode.

### 3. `fahrtenplaner/ui/render.py`

`render_result` gets a third call after winner + alternative:

```python
def render_result(result: OptimizationResult) -> None:
    ...
    _render_plan_section(result.winner, "winner", ...)
    if result.has_alternative:
        _render_plan_section(result.alternative, "alternative", ...)
    if result.efficiency_options:
        _render_efficiency_options(
            result.efficiency_options, fuel_consumption, fuel_price,
            latest_return_target,
        )
```

`_render_efficiency_options` is a new function in the same module. Each
option is a compact row (not the full bahn-row) showing:

- Aufwand (formatted as `4h13` via the existing `_fmt_duration` helper —
  reused as is).
- Tour number(s) + start → end station of the chain.
- Brutto-€ and €/h, each formatted with the existing `_fmt_eur`.
- A `Details ▾` toggle that reveals the existing `_render_details(plan,
  kind=f"eff_{idx}", ...)` block — full map, tagesplan, summary table. Full
  reuse of the detail renderer keeps visual consistency with winner /
  alternative.

The section header uses the existing `_eyebrow()` helper with the label
**"Weitere Optionen — nach Aufwand sortiert"** and a count chip
(`{n} Optionen`). CSS additions go to `styles.css` (or wherever the bahn-row
styles live) under a new `.efficiency-list` block; details styling can reuse
the existing `.bahn-details-anchor` class.

If `efficiency_options` is empty, no section is rendered (no empty
container, no header).

### 4. Tests

`tests/test_optimizer.py` gets a new `TestEfficiencyOptions` class with
fully-mocked transit reachability (matching the rest of the file's style):

- `test_returns_top_5_by_overhead_ascending` — with 8 reachable single-tour
  candidates of varying overhead, exactly 5 plans come back, sorted ASC by
  `overhead_duration`.
- `test_excludes_winner_and_alternative` — winner and alternative chains
  never appear in `efficiency_options`.
- `test_deduplicates_identical_tour_sequences` — two candidates with the
  same `(tour_nrs, has_car_legs)` collapse to one entry.
- `test_below_min_revenue_threshold_excluded` — a single-tour plan with
  `net_euros < 10.0` is dropped even if it has the lowest overhead.
- `test_empty_list_when_only_winner_valid` — when only one valid chain
  exists in total, `efficiency_options` is `[]`.

`tests/test_optimizer.py` also adds `TestDayPlanMetrics` (no separate
`test_models.py` — the repo keeps tests grouped by module-under-test, and the
metrics are exercised through the optimizer flow anyway):

- `test_overhead_duration_sums_only_connections` — tour duration excluded;
  outbound + transfer + inbound durations summed.
- `test_overhead_duration_includes_car_legs` — `car_outbound.minutes` +
  `car_inbound.minutes` added.
- `test_euros_per_hour_division` — known values (e.g. `net_euros=42.12`,
  `overhead=4h13` → `≈ 10.0 €/h`).

UI is not directly test-covered, consistent with the rest of `ui/`.

## Data flow

```
optimize_with_modes
├── optimize_day                       → (transit_winner, transit_candidates[])
├── optimize_day_car_mode (optional)   → (car_winner, car_candidates[])
├── pick winner (max net_euros)
├── pick alternative (other mode if competitive)
└── _build_efficiency_options(transit_candidates + car_candidates,
                              excluded=[winner, alternative])
    ├── filter net_euros >= 10
    ├── dedupe by (tour_nrs, has_car_legs)
    ├── exclude winner + alternative identities
    ├── sort by overhead_duration ASC
    └── take 5
   → OptimizationResult.efficiency_options
```

## Edge cases

- **Single valid chain on the day.** `efficiency_options = []`, the UI
  section is hidden, behaviour identical to today.
- **Winner already has lowest overhead.** Still fine — `efficiency_options`
  lists the next best options; the user simply sees the winner is also the
  most efficient.
- **All alternatives below €10 threshold.** Same as above: `[]`, section
  hidden.
- **Same chain wins in both transit and car mode.** The dedup key collapses
  them (`tour_nrs` equal, but `has_car_legs` differs → kept separate, which
  is correct: distinct user-facing experiences).
- **Overhead = 0** (theoretical: tour starts at home, ends at dest, no
  transfers, no Anreise/Rückreise needed). `euros_per_hour` returns `0.0`
  rather than dividing by zero. In practice never happens because
  `outbound`/`inbound` always carry at least the time-to-platform.

## Error handling

No new error paths. Both new properties on `DayPlan` are pure functions of
existing chain data. The optimizer additions cannot fail in ways the
existing code does not already handle (the DP table is the source of truth;
we just read from it).

## File checklist

- `fahrtenplaner/models.py` — add two `DayPlan` properties, extend
  `OptimizationResult`.
- `fahrtenplaner/optimizer.py` — add two constants, add
  `_enumerate_chain_candidates`, change `optimize_day` and
  `optimize_day_car_mode` return signatures (**breaking**), add
  `_build_efficiency_options`, update `optimize_with_modes`.
- `tests/test_optimizer.py` — update **existing** call sites of
  `optimize_day` / `optimize_day_car_mode` to unpack the new tuple return
  (today's hits: line 159, line 735, lines 784–785, line 838). The
  `patch(..., return_value=...)` calls must wrap their DayPlan in a tuple.
- `fahrtenplaner/ui/render.py` — add `_render_efficiency_options`, call it
  from `render_result`.
- CSS additions for the compact effort-row (one new `.efficiency-list`
  block).
- `tests/test_optimizer.py` — `TestEfficiencyOptions` (5 cases) +
  `TestDayPlanMetrics` (3 cases).

All code comments in English. UI strings remain in German per CLAUDE.md
language policy.
