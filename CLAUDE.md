# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

`Fahrtenplaner` ("Erhebungsfahrten-Planer") is a German Streamlit web app that helps Deutsche-Bahn surveyors plan their day. Surveyors get paid per "Tour" (a passenger-counting ride on a specific train run). The app:

1. Fetches the list of available unbooked tours from **MyRES 3** (`res.ivv-berlin.de`) for a given day and set of Bundesländer, with a local Excel file as offline fallback.
2. For a chosen home/destination station, computes the **highest-revenue chain of tours** that's actually reachable by public transit (Anreise → Tour → Transfer → Tour → ... → Rückreise).

**Language policy:** UI strings and user-facing labels stay in German (`"🚉 Anreise"`, `"Optimale Route berechnen"`, `"Frühste Abfahrt"`, etc. — the audience is German). Internal identifiers — variable names, function names, `ChainLink.type` discriminator strings — use English. The mapping for the chain-link types: `"outbound"` (Anreise / home → first tour), `"inbound"` (Rückreise / last tour → destination), `"tour"`, `"transfer"`. Comments may be German or English; prefer English for new code.

## ⛔ Hard rules

**NEVER push to a remote.** Do not run `git push`, `gh pr create`, or any other command that publishes commits/branches to GitHub or any remote — under any circumstances, regardless of how the user phrases their request. The user pushes manually when *they* decide.

Ambiguous phrases that DO NOT authorize a push:
- "move to main" / "merge to main" / "land on main"
- "ship it" / "make it live" / "deploy" / "publish"
- "finish the feature" / "we're done" / "release"
- Anything that doesn't literally include the word "push" or "to origin"

If the user says any of those, they mean *commit locally* or *finalize the local branch*. **Stop and ask** before running anything that touches a remote. The cost of asking once is zero; the cost of an unwanted push is real (rewriting public history is messy, and `git push --force` is itself a forbidden destructive action).

The only command form that authorizes a push is an explicit, unambiguous instruction containing the word "push" — e.g. *"push to origin"*, *"push the commits"*, *"git push now"*. Even then, prefer to confirm before pressing the button.

## Commands

This project uses **`uv`** (not pip). Python 3.12 is the runtime target on Windows; macOS/Linux dev uses 3.12+.

```bash
# Run the app (macOS/Linux dev)
./dev.sh                                    # localhost-only, runOnSave
uv run streamlit run fahrtenplaner/app.py   # equivalent

# Windows end-user launch (downloads uv.exe to .tools/ on first run)
starten.bat

# Tests (mocked; no live API calls — safe to run anywhere)
uv run --with pytest pytest tests/
uv run --with pytest pytest tests/test_optimizer.py -v                    # one file
uv run --with pytest pytest tests/test_optimizer.py::TestChaining -v      # one class
uv run --with pytest pytest tests/test_transit_client.py::TestStationsMatch::test_hbf_suffix  # one test
```

There is no linter or formatter configured.

## Architecture

The package is organized as a thin orchestrator (`app.py`) plus three layers:
**business logic** (top-level modules), **UI** (the `ui/` subpackage), and **packaging**
(`launcher.py`, `updater.py`).

```
fahrtenplaner/
├── app.py                  ← thin Streamlit entry: page config → state init →
│                             sidebar render → hero render → optimization render
├── models.py               ← Tour, Connection, Leg, ChainLink, DayPlan dataclasses
├── optimizer.py            ← 3-phase reachability + DAG-longest-path DP
├── transit_client.py            ← Google Maps geocoding + transit Directions (cached)
├── myres_client.py         ← MyRES login + Excel fallback
├── updater.py              ← GitHub Releases version check + staged self-update
├── _build_config.py        ← CI-generated, contains the embedded GMaps API key
└── ui/
    ├── styles.py           ← apply_page_config, inject_css (mtime-keyed cache)
    ├── state.py            ← init_session_state (idempotent on reruns)
    ├── errors.py           ← report_error → @st.dialog with a copyable code block
    ├── hero.py             ← render_hero (the "Fahrten*planer*" wordmark)
    ├── sidebar.py          ← render_sidebar; returns SidebarContext dataclass
    ├── update_panel.py     ← sidebar Über/Update widget (called by sidebar)
    ├── optimization.py     ← render_optimization_section: plan-strip + inputs +
    │                         button + result + tour browser (the main pane)
    ├── render.py           ← render_result, _render_tour_block, _render_connection_block
    └── map.py              ← render_route_map (Carto basemap + colored line layer)
```

Dependency rules between layers:

- **`ui/` modules** may import from each other (`from .errors import …`,
  `from .render import …`) and from any top-level module (`from optimizer import …`,
  `from models import …`). They never import from `app.py`.
- **Top-level business modules** (`optimizer.py`, `transit_client.py`, etc.) have **zero
  Streamlit imports**. They're framework-agnostic and fully test-covered. Don't add
  `import streamlit` anywhere outside `ui/` or `app.py`.
- **`app.py`** is a 48-line orchestrator. If you find yourself adding more than ~5
  lines of UI logic to it, that logic belongs in a `ui/` module instead.

### The optimizer (`optimizer.py::optimize_day`)

This is the core. It is **not** the bitmask DP that older planning docs describe — it is a **DAG-longest-path DP** over tours sorted by departure time:

1. **Phase 1 — Anreise:** for each tour `i`, can we get from `home_station` to its departure station before `tour_i.departure_dt − 5min`?
2. **Phase 2 — Transfers:** for each pair `(i, j)` with `i < j` (sorted by `departure_dt`), is the gap ≥ 5 min, ≤ `max_transfer_gap_hours`, and (if stations differ) is there a Google Maps transit route? Two prunes happen here that you must preserve when editing: same-station pairs short-circuit to an empty `Connection` (no API call), and the inner loop **breaks** on the first `j` whose gap exceeds `max_transfer_gap_hours` — relies on the sort.
3. **Phase 3 — Rückreise:** can we get from each tour's arrival station to `dest_station` by `latest_return`?
4. **DP:** `dp[j] = max revenue ending at tour j`, transitioning through `edge[i][j]`. The best `j` must additionally have a valid Rückreise. Backtrack via `pred[]` to reconstruct the chain.

Same-station and time-impossible pairs are skipped before any API call — this is the whole reason the optimizer can finish in reasonable time. Don't add code paths that bypass this pruning.

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

### Caching and rate limits

`transit_client.py` keeps **module-level dicts** `_station_cache` and `_connection_cache` (and a singleton `_gmaps`). They survive across Streamlit reruns *within the same process* but reset on worker restart. This is intentional — do NOT swap them for `@st.cache_data`, which had been used previously and caused subtle bugs around `st.secrets` access during cold start. There is currently no rate limiter; Google Maps quotas are the constraint.

### MyRES integration

`myres_client.py::MyRESClient` uses `curl_cffi.requests.Session(impersonate="chrome", verify=False)`. The `impersonate="chrome"` is load-bearing: the `res.ivv-berlin.de` WAF blocks stock Python TLS fingerprints. If you swap this client (e.g. to `httpx` or `requests`), login will start failing in production — keep `curl_cffi` unless you have a verified replacement. The endpoint is a DataTables AJAX API; the `_DT_COLUMNS` list and `STATE_IDS` map are server-defined contracts.

### Station name matching

`transit_client.py::stations_match` is fuzzy: lowercase + strip parenthetical suffixes + strip `hbf`/`hauptbahnhof`/`bf` + substring containment + `SequenceMatcher > 0.90`. The optimizer relies on this in two places (skipping same-station Anreise/Rückreise and the same-station transfer prune). If you tighten or rewrite this logic, run the full optimizer test suite — several tests depend on the current matching semantics.

### Geocoding cross-border stations

`lookup_station` queries `region="de"` (a *bias*, not a restriction) and tries `"<name> Bahnhof"` before falling back to `<name>`. Earlier code appended `"Deutschland"` and broke for Polish border stations like Szczecin Główny / Świnoujście — see `tests/test_transit_client.py::TestCrossBorderStations`. Don't reintroduce the `"Deutschland"` suffix.

### No auth

The app is shipped as a single-user Windows desktop binary, so there's no auth gate. An earlier version used `streamlit-authenticator` keyed off an `[auth]` block in `secrets.toml` — that code (and the `streamlit-authenticator` dep) was removed once desktop-only was locked in. Stale `[auth]` blocks in a local `secrets.toml` are harmless; Streamlit just ignores them.

## Configuration

- `GOOGLE_MAPS_API_KEY` — required. Read from `st.secrets` first, then env var. The app degrades to an error in `transit_client.py::_get_client` without it.
- `[myres]` block in `secrets.toml` (optional) — pre-fills login fields.
- `[auth]` block in `secrets.toml` (optional) — enables login gate.
- `.streamlit/secrets.toml` is gitignored. Never commit it.

## Domain glossary (German → English)

| Term | Meaning |
|---|---|
| Erhebungsfahrt / Tour | Paid passenger-counting ride |
| Anreise / Rückreise | Leg from home → first tour / last tour → destination |
| Transfer | Connection between two consecutive tours |
| Bundesland | German federal state (filter dimension in MyRES) |
| Verguetung / Euros | Pay for completing a tour |
| SEV / Schienenersatzverkehr | Rail-replacement bus (a known UI warning) |
| Heimatbahnhof / Zielbahnhof | Home / destination station |

## Files to know

**Entry / orchestration:**
- `fahrtenplaner/app.py` — 48-line orchestrator. `sys.path.insert` so `from ui.* import …` and bare `from optimizer import …` both resolve.

**Business logic (no Streamlit imports — fully test-covered):**
- `fahrtenplaner/optimizer.py` — `optimize_day()` and the constants (`MIN_TRANSFER_MINUTES = 5`, `TIGHT_TRANSFER_MINUTES = 15` for warnings).
- `fahrtenplaner/transit_client.py` — Google Maps geocoding (`lookup_station`, `batch_lookup_stations`) and transit routing (`find_connection`, `check_reachability_with_ids`).
- `fahrtenplaner/myres_client.py` — `MyRESClient` (live) plus `load_tours_from_excel` (fallback). Excel parsing tolerates column-name variations via regex in `_detect_columns`. Non-JSON responses are classified by `_classify_non_json_response` (login-page / WAF-block / empty / malformed).
- `fahrtenplaner/models.py` — `Tour`, `Connection`, `Leg`, `ChainLink`, `DayPlan` dataclasses.

**UI (lives in `ui/`, all Streamlit-aware):**
- `ui/sidebar.py` — entry: `render_sidebar()`. Returns `SidebarContext(selected_date, home_station, dest_station, same_station)`. Owns the MyRES login flow including `_handle_load_tours` which routes failures through `errors.report_error`.
- `ui/optimization.py` — entry: `render_optimization_section(tours, ctx)`. The main pane: plan-strip → section heading → param inputs → primary button → result → tour browser expander. Includes `_parse_hhmm` (forgiving HH:MM parser).
- `ui/render.py` — entry: `render_result(plan)`. Renders metrics + toggleable map + tour/connection blocks + summary table.
- `ui/map.py` — entry: `render_route_map(plan)`. PyDeck LineLayer, Carto basemap, Verkehrsrot for tour segments and slate for outbound/inbound.
- `ui/errors.py` — entry: `report_error(title, details, exc)`. Opens `@st.dialog` with a copyable `st.code()` block — Dad clicks the clipboard icon and forwards.
- `ui/update_panel.py` — entry: `render_update_panel()`. The sidebar Über/Update widget that checks GitHub Releases and stages downloads.
- `ui/styles.py`, `ui/state.py`, `ui/hero.py` — small helpers (page config + CSS, session-state init, wordmark).

**Packaging:**
- `launcher.py` — pywebview wrapper at the repo root. Spawns Streamlit as a child process (Streamlit's signal handler requires the main thread of its process), waits for the port, opens a chromeless WebView2 window. `--dev` enables `runOnSave`.
- `updater.py` — version check, staged download, pre-launch swap (rename old `.exe` → `.exe.old`, rename new in place, re-exec).

**Tests:**
- `tests/` — pytest, fully mocked. `test_optimizer.py` builds tours with `make_tour()` and patches `transit_client` reachability/lookup; `test_transit_client.py` patches `googlemaps.Client`. UI is not directly test-covered.

## Where to add new code

- **A new sidebar widget** → new `_render_*` helper inside `ui/sidebar.py`, called from `render_sidebar()`.
- **A new section in the main pane** → new function in `ui/optimization.py` (or a new module if substantial).
- **A new visualization of the result** → new module in `ui/`, called from `ui/render.py::render_result`.
- **A new optimizer constraint** → modify `optimize_day` in `optimizer.py` (no UI change unless you also surface a new input — that goes in `ui/optimization.py`).
- **A new external API** → new client module at `fahrtenplaner/<name>_client.py` parallel to `myres_client.py`. Keep Streamlit out of it.

## ⚠️ Stale planning docs

`.planning/codebase/*.md` was generated by an earlier analysis tool and is **partially out of date**. Specifically:

- It describes the connection backend as `v6.db.transport.rest` via `httpx` — code uses **Google Maps Directions** via the `googlemaps` SDK.
- It describes optimization as **bitmask DP** with a 20-tour cap — code is a **DAG-longest-path DP** with no such cap.
- It states "no test framework / no tests" — `tests/` exists with pytest and ~30 test cases.
- It describes MyRES integration as **subprocess curl** — code uses `curl_cffi` (in-process Chrome TLS impersonation).

Treat those docs as *historical* context only. The code, this file, and the tests are the source of truth.
