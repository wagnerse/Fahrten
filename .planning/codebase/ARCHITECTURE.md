# Architecture

**Analysis Date:** 2025-03-14

## Pattern Overview

**Overall:** Modular three-phase optimization pipeline with external API integration

**Key Characteristics:**
- Three-tier data flow: collection → reachability validation → dynamic programming optimization
- Bitmask-DP algorithm for optimal subset selection
- Stateless external API calls with caching and rate limiting
- Single-page Streamlit web UI with session-based state management

## Layers

**Presentation Layer:**
- Purpose: Interactive web UI for tour loading, filtering, and optimization visualization
- Location: `fahrtenplaner/app.py`
- Contains: Streamlit page configuration, forms, filters, result rendering, tabs
- Depends on: Models, MyRESClient, Optimizer, DB Client
- Used by: End users via browser

**Data Access Layer:**
- Purpose: Handle external API calls, caching, and data import from multiple sources
- Location: `fahrtenplaner/db_client.py`, `fahrtenplaner/myres_client.py`
- Contains: HTTP clients, station lookups, journey queries, Excel parsing, rate limiting
- Depends on: httpx, pandas, requests libraries
- Used by: Optimizer, Presentation layer for data retrieval

**Business Logic Layer:**
- Purpose: Compute optimal tour chains using dynamic programming
- Location: `fahrtenplaner/optimizer.py`
- Contains: Three-phase reachability validation, bitmask-DP algorithm, chain reconstruction
- Depends on: Models, DB Client functions
- Used by: Presentation layer for optimization computation

**Domain Model Layer:**
- Purpose: Define core entities and their properties
- Location: `fahrtenplaner/models.py`
- Contains: Tour, Leg, Connection, ChainLink, DayPlan dataclasses
- Depends on: Python stdlib (datetime, dataclasses)
- Used by: All other layers

## Data Flow

**Tour Loading Flow:**

1. User enters MyRES credentials and filter criteria in sidebar (`app.py`)
2. MyRESClient.login() authenticates via curl subprocess (`myres_client.py`)
3. MyRESClient.fetch_free_tours() retrieves DataTables JSON via curl, parses to Tour list
4. Fallback: load_tours_from_excel() parses demo Excel file with column detection
5. Tours stored in session_state for subsequent operations

**Optimization Flow:**

1. User selects date and enters time constraints in "Optimierung" tab
2. Filter day_tours for selected_date from session_state
3. optimize_day() executes three-phase computation:
   - **Phase 1/3 (Anreise):** check_reachability_with_ids() verifies home_station → tour departure reachable
   - **Phase 2/3 (Transfers):** For each tour pair, check_reachability_with_ids() validates transfer feasibility
   - **Phase 3/3 (Rückreise):** check_reachability_with_ids() verifies tour arrival → dest_station reachable
4. Build reachability graph (edge matrix, can_reach_from_home, can_reach_to_dest)
5. Run bitmask-DP algorithm: dp[mask][last] = max revenue with tours in mask ending at last
6. Backtrack parent pointers to reconstruct optimal chain
7. Build DayPlan with ChainLink sequence (anreise → tour → transfer → ... → rückreise)
8. Render results in UI with warnings and metrics

**State Management:**

- Session state: `st.session_state.tours` (list[Tour]), `st.session_state.myres_client` (MyRESClient or None)
- Streamlit caches: lookup_station() and find_connection() cached 24h and 1h respectively
- No persistent database; all state in-memory per session

## Key Abstractions

**Tour:**
- Purpose: Represents a single revenue-generating tour with fixed times and stations
- Properties: tour_nr, date, departure/arrival times and stations, duration, euros, num_rides, points
- Derived properties: departure_dt, arrival_dt (handle overnight tours), duration_str
- Pattern: Immutable dataclass with computed properties

**Connection:**
- Purpose: Represents a multi-leg journey between two stations (train/bus transfers)
- Properties: legs (list[Leg]), departure_time, arrival_time, duration, transfers count
- Capabilities: Detect replacement service (Schienenersatzverkehr), format duration
- Pattern: Aggregate of Leg objects with convenience properties

**ChainLink:**
- Purpose: Element in optimized daily plan (tour or transfer segment)
- Types: "anreise" (arrival), "tour" (revenue segment), "transfer" (between tours), "rückreise" (departure)
- Properties: type, tour, connection, warning
- Pattern: Tagged union using type field to distinguish variants

**DayPlan:**
- Purpose: Complete optimized itinerary for a single day
- Properties: chain (list[ChainLink])
- Derived: total_euros, num_tours, warnings, time_range
- Pattern: Composite of ChainLink objects with aggregation queries

## Entry Points

**Web UI Entry:**
- Location: `fahrtenplaner/app.py` main execution
- Triggers: Streamlit run app.py in browser at localhost:8501
- Responsibilities: Page setup, session initialization, form input, result rendering, tab routing

**API Integration:**
- Location: db_client.py functions + myres_client.py methods
- Triggers: optimize_day() → check_reachability_with_ids() → find_connection()
- Responsibilities: Stateless HTTP calls to DB transport.rest API and MyRES endpoints

**Optimization Engine:**
- Location: optimizer.py optimize_day()
- Triggers: "Optimale Route berechnen" button in UI
- Responsibilities: Three-phase graph construction, DP algorithm execution, result synthesis

## Error Handling

**Strategy:** Graceful degradation with user-facing warnings and fallback mechanisms

**Patterns:**
- API errors in lookup_station() and find_connection(): Exceptions not cached, retry logic applied in check_reachability_with_ids() (+15min retry window)
- HTTP errors: Caught silently, return None for unreachable tours; removed from consideration
- MyRES login failures: Display error message, suggest demo Excel fallback
- Station name mismatches: stations_match() uses fuzzy matching (>90% similarity), substring containment, and normalization
- Unreachable tours: Automatically filtered out during Phase 1; user notified in logs
- No valid chain found: DayPlan with 0 tours returned; UI warns user to adjust filters/times

## Cross-Cutting Concerns

**Logging:** Progress callbacks (progress_cb) in optimize_day(); reported to UI progress bar and log expander. Manual logging via print (caught by Streamlit stderr).

**Validation:**
- Tour data validated during Excel/JSON parsing (skip malformed rows)
- Time constraints validated in UI (ensure latest_return > earliest_departure; add 1 day if inverted)
- Station names validated via lookup_station() calls; missing stations trigger early return

**Authentication:** MyRES login via subprocess curl (works around WAF blocking Python HTTP clients); session cookie maintained per client instance

**Rate Limiting:** RateLimiter class enforces 80 req/min with token-bucket algorithm; automatically sleeps between API calls

**Caching:**
- lookup_station(): @st.cache_data(ttl=86400) – only successful queries cached
- find_connection(): @st.cache_data(ttl=3600) – journeys cached by (from_id, to_id, departure_iso)
- Fallback to demo Excel without network access
