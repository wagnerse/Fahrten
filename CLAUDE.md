# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

`Fahrtenplaner` ("Erhebungsfahrten-Planer") is a German Streamlit web app that helps Deutsche-Bahn surveyors plan their day. Surveyors get paid per "Tour" (a passenger-counting ride on a specific train run). The app:

1. Fetches the list of available unbooked tours from **MyRES 3** (`res.ivv-berlin.de`) for a given day and set of Bundesländer, with a local Excel file as offline fallback.
2. For a chosen home/destination station, computes the **highest-revenue chain of tours** that's actually reachable by public transit (Anreise → Tour → Transfer → Tour → ... → Rückreise).

The UI, comments, and many identifiers are in German. Keep that style when editing.

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
uv run --with pytest pytest tests/test_db_client.py::TestStationsMatch::test_hbf_suffix  # one test
```

There is no linter or formatter configured.

## Architecture

Four modules in `fahrtenplaner/` form a strict dependency order:

```
app.py            (Streamlit UI, session state, auth gate, rendering)
   └─→ optimizer.py        (3-phase reachability + DAG-longest-path DP)
         └─→ db_client.py  (Google Maps geocoding + transit Directions)
   └─→ myres_client.py     (MyRES login + Excel fallback)
         └─→ models.py     (Tour, Connection, Leg, ChainLink, DayPlan dataclasses)
```

### The optimizer (`optimizer.py::optimize_day`)

This is the core. It is **not** the bitmask DP that older planning docs describe — it is a **DAG-longest-path DP** over tours sorted by departure time:

1. **Phase 1 — Anreise:** for each tour `i`, can we get from `home_station` to its departure station before `tour_i.departure_dt − 5min`?
2. **Phase 2 — Transfers:** for each pair `(i, j)` with `i < j` (sorted by `departure_dt`), is the gap ≥ 5 min, ≤ `max_transfer_gap_hours`, and (if stations differ) is there a Google Maps transit route? Two prunes happen here that you must preserve when editing: same-station pairs short-circuit to an empty `Connection` (no API call), and the inner loop **breaks** on the first `j` whose gap exceeds `max_transfer_gap_hours` — relies on the sort.
3. **Phase 3 — Rückreise:** can we get from each tour's arrival station to `dest_station` by `latest_return`?
4. **DP:** `dp[j] = max revenue ending at tour j`, transitioning through `edge[i][j]`. The best `j` must additionally have a valid Rückreise. Backtrack via `pred[]` to reconstruct the chain.

Same-station and time-impossible pairs are skipped before any API call — this is the whole reason the optimizer can finish in reasonable time. Don't add code paths that bypass this pruning.

### Caching and rate limits

`db_client.py` keeps **module-level dicts** `_station_cache` and `_connection_cache` (and a singleton `_gmaps`). They survive across Streamlit reruns *within the same process* but reset on worker restart. This is intentional — do NOT swap them for `@st.cache_data`, which had been used previously and caused subtle bugs around `st.secrets` access during cold start. There is currently no rate limiter; Google Maps quotas are the constraint.

### MyRES integration

`myres_client.py::MyRESClient` uses `curl_cffi.requests.Session(impersonate="chrome", verify=False)`. The `impersonate="chrome"` is load-bearing: the `res.ivv-berlin.de` WAF blocks stock Python TLS fingerprints. If you swap this client (e.g. to `httpx` or `requests`), login will start failing in production — keep `curl_cffi` unless you have a verified replacement. The endpoint is a DataTables AJAX API; the `_DT_COLUMNS` list and `STATE_IDS` map are server-defined contracts.

### Station name matching

`db_client.py::stations_match` is fuzzy: lowercase + strip parenthetical suffixes + strip `hbf`/`hauptbahnhof`/`bf` + substring containment + `SequenceMatcher > 0.90`. The optimizer relies on this in two places (skipping same-station Anreise/Rückreise and the same-station transfer prune). If you tighten or rewrite this logic, run the full optimizer test suite — several tests depend on the current matching semantics.

### Geocoding cross-border stations

`lookup_station` queries `region="de"` (a *bias*, not a restriction) and tries `"<name> Bahnhof"` before falling back to `<name>`. Earlier code appended `"Deutschland"` and broke for Polish border stations like Szczecin Główny / Świnoujście — see `tests/test_db_client.py::TestCrossBorderStations`. Don't reintroduce the `"Deutschland"` suffix.

### Auth gate

If `[auth]` is present in `.streamlit/secrets.toml`, `streamlit-authenticator` gates the app; otherwise the app is open. The `_to_plain` helper exists because `st.secrets` returns proxy objects that `streamlit-authenticator` cannot consume directly.

## Configuration

- `GOOGLE_MAPS_API_KEY` — required. Read from `st.secrets` first, then env var. The app degrades to an error in `db_client.py::_get_client` without it.
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

- `fahrtenplaner/app.py` — Streamlit UI, sidebar inputs, two tabs ("Touren-Übersicht", "Optimierung"). Top of file does `sys.path.insert` so the package modules import flat.
- `fahrtenplaner/optimizer.py` — `optimize_day()` and the constants (`MIN_TRANSFER_MINUTES = 5`, `TIGHT_TRANSFER_MINUTES = 15` for warnings).
- `fahrtenplaner/db_client.py` — Google Maps geocoding (`lookup_station`, `batch_lookup_stations`) and transit routing (`find_connection`, `check_reachability_with_ids`).
- `fahrtenplaner/myres_client.py` — `MyRESClient` (live) plus `load_tours_from_excel` (fallback). Excel parsing tolerates column-name variations via regex in `_detect_columns`.
- `tests/` — pytest, fully mocked. `test_optimizer.py` builds tours with `make_tour()` and patches `db_client` reachability/lookup; `test_db_client.py` patches `googlemaps.Client`.

## ⚠️ Stale planning docs

`.planning/codebase/*.md` was generated by an earlier analysis tool and is **partially out of date**. Specifically:

- It describes the connection backend as `v6.db.transport.rest` via `httpx` — code uses **Google Maps Directions** via the `googlemaps` SDK.
- It describes optimization as **bitmask DP** with a 20-tour cap — code is a **DAG-longest-path DP** with no such cap.
- It states "no test framework / no tests" — `tests/` exists with pytest and ~30 test cases.
- It describes MyRES integration as **subprocess curl** — code uses `curl_cffi` (in-process Chrome TLS impersonation).

Treat those docs as *historical* context only. The code, this file, and the tests are the source of truth.
