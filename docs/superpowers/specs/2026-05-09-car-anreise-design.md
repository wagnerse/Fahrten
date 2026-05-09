# Car-Anreise (Auto-Anfahrt) — Design

**Date:** 2026-05-09
**Status:** Approved for implementation
**Audience:** Future implementor (likely Claude Code)

## Summary

Add an option for the user to drive their own car to a tour-departure station within a configurable radius, instead of requiring a transit route from home. The car must be picked up at day's end, so the chain start station == chain end station == car-park station. Fuel cost is subtracted from gross revenue when comparing plans; the optimizer returns *both* the transit-only and car-mode best plans, and the UI shows the winner (highest net €) with the alternative collapsible below.

## Problem statement

Routes that start very early or end very late often can't be reached by transit — there are no early/late connecting trains from the user's home (e.g. Prenzlau). The user owns a car and wants to drive (≤ X minutes) to a tour-departure station near home (e.g. Pasewalk, 30 min by car), do their tour chain there, and drive back from the same station at day's end. They configure the maximum drive time once, plus their car's fuel consumption and the current fuel price. The optimizer should compare a transit-only plan against the best car-mode plan using net revenue (gross − fuel cost), and surface both for transparency.

## Requirements (from clarifying questions)

| Decision | Outcome |
|---|---|
| When `max_car_minutes > 0`, what does the optimizer do? | **Return both transit-best and car-best plans** — winner + alternative for comparison |
| Result layout | **Winner card with full detail; alternative as a collapsible expander** below |
| Cost model | **Per-km cost computed from `consumption (l/100km)` × `fuel_price (€/l)`** — both as user inputs |
| Winner selection | **Net euros** (gross − fuel cost). Transit wins on tie. |
| Same chain end as start? | Yes — chain start station == chain end station == car-park station |
| Different `dest_station`? | Car-mode disabled (chain can't return to a single station) |
| API throughput protection | Cap `max_transfer_gap_minutes` at **240 (4 h)**, `max_car_minutes` at **120 (2 h)** in the UI |

## Section 1 — Architecture

The change touches three layers. Existing `optimize_day` (transit-only, 34 tests) is **untouched**. New car-mode logic is sibling code; a new orchestrator combines them.

| Layer | Change |
|---|---|
| `models.py` | New `CarLeg` dataclass; new `OptimizationResult` dataclass; extend `ChainLink` with `car_leg` field; extend `DayPlan` with `total_costs`, `net_euros`, `has_car_legs` properties; add new `ChainLink.type` strings `"car_outbound"`, `"car_inbound"` |
| `transit_client.py` | New `driving_info(from_id, to_id) → Optional[(minutes, km)]` using Google Maps Directions API in driving mode. Cached the same way as transit lookups. |
| `optimizer.py` | Refactor: extract `_compute_transfer_matrix(tours, get_id, max_gap, report)` as a shared helper (already done as part of the cognitive-complexity refactor). Add `optimize_day_car_mode(tours, home_station, ..., max_car_minutes, fuel_consumption, fuel_price, transfer_matrix)`. Add `optimize_with_modes(tours, ctx, ...)` orchestrator returning `OptimizationResult`. |
| `ui/sidebar.py` | New "Auto" expander with `Auto-Verbrauch (l/100 km)` (default 7.0) and `Spritpreis (€/l)` (default 1.79). Persisted via `session_state.fuel_consumption` / `fuel_price`. |
| `ui/optimization.py` | New 4th-column input `Max. Auto-Anfahrt (Min.)` (min=0, max=120, step=5, default=0). Cap `max_gap_minutes` UI at 240. Disable the car input when `same_station == False`. Call `optimize_with_modes` instead of `optimize_day`. Store `OptimizationResult` in `session_state.last_plan`. |
| `ui/render.py` | `render_result(result: OptimizationResult)` shows the winner card with net-€ headline + breakdown. New `render_alternative_collapse(alt: DayPlan)` renders the alternative inside an expander. New `_render_car_leg_block(link)` for `car_outbound` / `car_inbound` link types. |
| `ui/map.py` | `_MAP_COLORS` gains `"car_outbound"` and `"car_inbound"` (amber `#D4A017`). LineLayer renders these as **dashed** (deck.gl `getDashArray`). Legend gains a "╌ Auto" row using inline SVG with `stroke-dasharray`. |

### Why this split

- Existing `optimize_day` stays a pure transit optimizer. All current tests keep passing without modification.
- `optimize_with_modes` is the single new public entry point for the UI.
- Transfer matrix computation is shared between transit-mode and car-mode evaluation — same matrix is reused, so no duplicate API calls.
- Driving-time API calls only fire when `max_car_minutes > 0`.

## Section 2 — Algorithm

The general problem (Orienteering with Time Windows + multi-modal access) is NP-hard, but our specific case is polynomial because tours sorted by `departure_dt` form a DAG. Standard literature reduction: enumerate the K candidate car-park stations and run a constrained DAG longest-path DP per candidate.

### Candidate selection

```
candidates = unique_set(t.departure_station for t in day_tours)
for each S in candidates:
    geocode S (cached → place_id, lat, lng)
    if geodesic_distance(home, S) > max_car_minutes × 1.5 km/min:
        skip                                          # cheap pre-filter, no API call
    drive_minutes[S], drive_km[S] = driving_info(home_id, S_id)   # cached
valid = [S for S in candidates if drive_minutes[S] is not None
                                  and drive_minutes[S] <= max_car_minutes]
```

The geodesic pre-filter eliminates obvious-no candidates before paying for a Directions call.

### Per-candidate constrained DP

For each `S` in `valid`, run a DAG-longest-path DP that's structurally identical to the transit DP, but with:

- **Seed**: `dp[i] = tours[i].euros` only when `tours[i].departure_station == S` AND `tours[i].departure_dt >= earliest_departure + drive_minutes[S]`.
- **Transition**: same as transit — uses the shared `transfer_matrix`.
- **Best end**: max `dp[j]` over `j` where `tours[j].arrival_station == S` AND `tours[j].arrival_dt + drive_minutes[S] <= latest_return`.

The drive time is folded into the time budget on both ends. A 30-min radius effectively shortens the working day by 60 minutes but unlocks tours no transit chain could touch.

### Picking the winner

```
transit_plan = optimize_day(tours, home, dest, ...)
car_plan = optimize_day_car_mode(tours, home, ..., max_car_minutes,
                                  fuel_consumption, fuel_price)

candidates = [p for p in [transit_plan, car_plan] if p.num_tours > 0]
candidates.sort(key=lambda p: -p.net_euros)         # highest net first
if len(candidates) == 0:
    return OptimizationResult(winner=DayPlan(), alternative=None)
winner = candidates[0]
alternative = candidates[1] if len(candidates) > 1 else None
return OptimizationResult(winner=winner, alternative=alternative)
```

Tie-breaker: when both modes have the same `net_euros`, **transit wins** (no car needed; simpler).

### Cost formula

```
cost_per_km = (fuel_consumption / 100) × fuel_price
gas_cost    = 2 × drive_km × cost_per_km    # outbound + return; one car-park station, symmetric
```

Stored on `CarLeg.cost` per leg (one-way), so the auto-anreise / auto-rückreise blocks each show their own number. `DayPlan.total_costs` sums them.

### Computational cost

| Day size | n | K (typical) | Driving API calls | DP wall-time (Python) |
|---|---|---|---|---|
| Typical BB+MV day | 30 | 10 | ≤ 10 | < 10 ms |
| Big day | 100 | 20 | ≤ 20 | < 100 ms |

Bounded by Google Maps API throughput, not algorithm. The DAG property guarantees no combinatorial explosion. UI caps prevent pathological inputs.

## Section 3 — UI / result rendering

### Sidebar additions

New "Auto" expander between "Bahnhöfe" and "Touren laden":

```
▼ Auto                          (collapsed by default)
  Auto-Verbrauch (l/100 km): [7.0]    step 0.1
  Spritpreis (€/l):          [1.79]   step 0.01
```

Both persist via `st.session_state.fuel_consumption` / `fuel_price`. Both apply the same way regardless of whether car-mode is currently used (cost = 0 when not used).

### Main-pane addition

A 4th column in the optimization panel (after `Max. Pause zwischen Touren`):

```
Max. Auto-Anfahrt (Min.): [0]    min=0, max=120, step=5
```

Disabled with tooltip "Auto-Modus erfordert Ankunft = Abfahrt" when `same_station == False`. Also: cap `Max. Pause` at 240 in the same pass.

### Winner card

```
┌─ ⭐ Auto-Plan · 208,01 € netto ────────────────────────────────────┐
│  Gesamtverdienst       Anzahl Touren     Zeitraum                  │
│  208,01 € netto        5                 06:30–20:30               │
│  215,00 € brutto                                                   │
│                                                                    │
│  Verdienst 215,00 € − Sprit 6,99 € (60 km · 7,0 l/100km · 1,79 €/l)│
│  ─────────────────────────────────────────────────────────────     │
│                                                                    │
│  🚗 Auto-Anfahrt: 30 min · 32 km · 3,71 € nach Pasewalk            │
│  ┌─ Tour 704347 · Mi · 69,35 € ──────────────────────────┐         │
│  │  06:30 Pasewalk → 09:55 Falkenberg(Elster)            │         │
│  └────────────────────────────────────────────────────────┘         │
│  …chain continues…                                                 │
│  🚗 Auto-Rückfahrt: 30 min · 32 km · 3,28 € nach Prenzlau          │
└────────────────────────────────────────────────────────────────────┘
  ▸ Alternative anzeigen · Transit 145,00 € (4 Touren)
```

Key changes: title shows net €, three-line metric stack, formula breakdown line, new `🚗` car-leg blocks. When a transit-only plan is the winner, the same structure but with no car blocks and no breakdown line.

### Alternative expander

Closed by default. When opened, renders the *full* alternative plan via `render_result` recursively (same tour cards, connection blocks, summary table, optional map toggle). Expander label always shows the alternative's net €:

```
▾ Alternative anzeigen · Auto 132,00 € netto (5 Touren)
```

Not rendered when `alternative is None or alternative.num_tours == 0`.

### Car-leg block CSS

```css
.auto-leg {
    display: flex;
    align-items: center;
    gap: 0.7rem;
    padding: 0.6rem 0.9rem;
    background: var(--surface);
    border: 1px solid #E8DAB6;
    border-left: 4px solid #D4A017;          /* amber */
    border-radius: var(--radius-sm);
    margin: 0.5rem 0;
    font-family: var(--sans);
}
.auto-leg .icon { font-size: 1.05rem; }
.auto-leg .label { font-weight: 600; color: var(--ink); font-size: 0.92rem; }
.auto-leg .meta { font-family: var(--mono); font-variant-numeric: tabular-nums;
                  color: var(--sub); font-size: 0.85rem; }
.auto-leg .dest { margin-left: auto; font-weight: 500; color: var(--ink); }
```

### Map updates

- `_MAP_COLORS["car_outbound"] = [212, 160, 23, 220]` (amber, full opacity)
- `_MAP_COLORS["car_inbound"] = [212, 160, 23, 220]`
- Auto segments rendered as dashed lines (`getDashArray=[6, 4]` in deck.gl)
- Legend grows by one row, uses an inline `<svg><line stroke-dasharray="4 3">` mark to represent the dashed style

Existing transit-mode segments (solid blue-slate for outbound/inbound, light gray for transfers, Verkehrsrot for tours) unchanged.

## Section 4 — Data model

### New `CarLeg`

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

`cost` is per-leg (one-way). Total round-trip cost = `outbound.cost + inbound.cost`.

### Extended `ChainLink`

```python
@dataclass
class ChainLink:
    type: str
    tour: Optional[Tour] = None
    connection: Optional[Connection] = None
    car_leg: Optional[CarLeg] = None           # NEW
    warning: Optional[str] = None

    @property
    def label(self) -> str:
        labels = {
            "tour": f"Tour {self.tour.tour_nr}" if self.tour else "Tour",
            "outbound": "Anreise",
            "inbound": "Rückreise",
            "car_outbound": "Auto-Anfahrt",
            "car_inbound":  "Auto-Rückfahrt",
        }
        return labels.get(self.type, "Transfer")
```

`type` vocabulary expands to: `"tour" | "transfer" | "outbound" | "inbound" | "car_outbound" | "car_inbound"`.

### Extended `DayPlan`

```python
@dataclass
class DayPlan:
    chain: list[ChainLink] = field(default_factory=list)

    @property
    def total_euros(self) -> float:        # unchanged — gross revenue
        return sum(t.euros for t in self.tours)

    @property
    def total_costs(self) -> float:        # NEW
        return sum(link.car_leg.cost for link in self.chain
                   if link.car_leg is not None)

    @property
    def net_euros(self) -> float:          # NEW — winner-selection key
        return self.total_euros - self.total_costs

    @property
    def has_car_legs(self) -> bool:        # NEW — UI convenience
        return any(link.car_leg is not None for link in self.chain)
```

For transit-only plans, `total_costs == 0` and `net_euros == total_euros` — old call sites work unchanged.

### New `OptimizationResult`

```python
@dataclass
class OptimizationResult:
    """A primary plan + an optional alternative for side-by-side comparison."""
    winner: DayPlan
    alternative: Optional[DayPlan] = None

    @property
    def has_alternative(self) -> bool:
        return self.alternative is not None and self.alternative.num_tours > 0
```

Returned by the new `optimize_with_modes()`. Existing `optimize_day()` keeps returning a plain `DayPlan` (test compatibility).

## Section 5 — Edge cases & non-MVP scope

### In-MVP edge cases

| Condition | Behavior |
|---|---|
| `max_car_minutes == 0` (default) | Car-mode skipped. Only transit plan. No alternative. |
| `same_station == False` | Car-mode UI input disabled. Optimizer ignores `max_car_minutes` even if non-zero. |
| Net-€ tie | Transit wins (simpler, no car needed). |
| Net-€ negative for car (huge drive eats revenue) | Plan still valid; if it's the only option, shown with negative net clearly. Transit wins if positive. |
| `fuel_consumption == 0` or `fuel_price == 0` | All car legs cost 0. UI shows just gross "Verdienst 215 €", no breakdown. |
| Home station IS a tour-departure station | `drive_minutes = 0`, `drive_km = 0`, `cost = 0`. Degenerate case — transit usually wins (also empty Anreise). |
| All driving-time API calls fail | Car-mode returns empty `DayPlan`. Transit shown as winner. |
| No tour-departure stations within radius | Car-mode skipped. Transit shown as winner. |
| Car finds chain, transit doesn't | Car shown as winner. **No alternative** (transit produced nothing). |
| Both modes produce identical plans | One plan, `alternative = None`. |
| Map toggle on, plan has car legs | Dashed amber segments + extra legend row "╌ Auto". |

### Test coverage — new tests

`tests/test_optimizer.py`:

- `test_car_mode_disabled_when_max_minutes_zero` — `max_car_minutes=0` → no car-mode evaluation
- `test_car_mode_skipped_when_dest_differs` — `dest != home` → no car-mode regardless of max_car_minutes
- `test_car_mode_finds_chain_when_transit_doesnt` — far early tour reachable only by 30-min drive
- `test_car_mode_loses_to_transit_when_gas_dominates` — transit's higher net wins despite car's higher gross
- `test_winner_is_decided_by_net_euros` — net comparison logic
- `test_optimization_result_alternative_when_both_modes_succeed`
- `test_car_leg_cost_computed_from_consumption_and_price` — formula verification
- `test_chain_starts_and_ends_at_same_car_park_station` — same-station constraint

`tests/test_transit_client.py`:

- `test_driving_info_returns_minutes_and_km` — mocked Google Directions response
- `test_driving_info_caches_results` — second call doesn't hit API
- `test_driving_info_returns_none_when_no_route`

Test count target: 34 → ~45.

### Non-MVP — explicit out-of-scope

| Deferred | Why |
|---|---|
| Multi-day planning | Per-day optimization; multi-day is a separate feature |
| Park-and-Ride stations that aren't tour stations | Adds candidate set complexity; tour-departure stations cover realistic cases |
| Mixed-mode chains (drive partway, train back) | Violates "must return to parked car" — explicit user requirement |
| Real-time fuel prices (Tankerkönig API) | Manual input is fine for v1 |
| CO₂ / environmental cost | Future polish |
| Toll roads / autobahn fees | Negligible at typical 30-min commutes |
| Time-dependent driving times (rush hour) | Google's "typical traffic" default is good enough |
| Persisted fuel-price across desktop launches | Future polish — JSON in user appdata |
| Real driving-route polylines on the map | Currently straight lines; real geometry adds API cost for visual-only benefit |
| Asymmetric drive times (different there vs. back) | One-way × 2 is fine for typical use |

## Implementation sequencing (high-level)

1. `models.py` — add `CarLeg`, `OptimizationResult`, extend `ChainLink` and `DayPlan`. No behavior change yet; old tests pass.
2. `transit_client.py` — add `driving_info`. Mocked tests for it.
3. `optimizer.py` — extract `_compute_transfer_matrix` already done. Add `optimize_day_car_mode` and `optimize_with_modes`. Algorithmic tests.
4. `ui/sidebar.py` — fuel inputs.
5. `ui/optimization.py` — Max. Auto-Anfahrt input, cap on max_gap_minutes, switch from `optimize_day` to `optimize_with_modes`, store `OptimizationResult` in session state.
6. `ui/render.py` — winner card (net €, breakdown, car-leg blocks), alternative expander.
7. `ui/map.py` — amber dashed segments, updated legend.
8. `assets/style.css` — `.auto-leg` block styling.
9. `CLAUDE.md` — document the new flow and the OptimizationResult migration.

Each step ships independently (incremental commits). Tests added per step.

## Open decisions deferred to implementation

- Exact deck.gl `getDashArray` value (4-3, 6-4, 8-4) — pick visually during implementation
- Default value of `Max. Auto-Anfahrt` slider step — currently spec says step=5; could be 1 if precision matters
- Whether to render the cost breakdown formula on a single line or wrap it on narrow viewports
- Whether `_render_car_leg_block` lives in `ui/render.py` (alongside other chain-link helpers) or gets its own file (probably fine in `render.py` for a single-block render)

These are stylistic decisions that don't affect the architecture. Resolve during implementation.
