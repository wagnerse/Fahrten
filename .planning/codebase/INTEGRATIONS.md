# External Integrations

**Analysis Date:** 2025-03-14

## APIs & External Services

**MyRES 3 (Deutsche Bahn Tour Distribution):**
- URL: `https://res.ivv-berlin.de`
- What it's used for: Fetches available rail survey tours for DB counting operations
- SDK/Client: Custom HTTP client via `subprocess.run(curl)` in `fahrtenplaner/myres_client.py`
- Auth: Username/password (entered via Streamlit sidebar, lines 70-71 in `app.py`)
- Implementation: `MyRESClient` class uses curl subprocess calls to bypass WAF TLS fingerprinting blocks
- Fallback: Local Excel file import for development when MyRES unavailable
- Session: Cookie-based (`rm3_session` cookie, maintained across requests)

**DB Transport.rest API (Open-source DB journey planner):**
- URL: `https://v6.db.transport.rest`
- What it's used for: Finds train/bus connections between stations for journey planning
- SDK/Client: httpx (Python HTTP client)
- Auth: None required (public API)
- Implementation: `fahrtenplaner/db_client.py` handles all interactions
- Features:
  - Station lookup by name: `/locations` endpoint
  - Journey search: `/journeys` endpoint with transfer/transport type filters
  - Caching: Streamlit `@st.cache_data` with 24h TTL for stations, 1h for journeys
  - Rate limiting: Custom token-bucket limiter (80 requests/minute max)
- Error handling: Caches only successful lookups; failed API calls raise exceptions and are retried up to 3 times

## Data Storage

**Databases:**
- None - No persistent database
- All data in memory during session or from temporary files

**File Storage:**
- Local Excel file: `Freie_Touren_BB_MV_01-03_April_2026.xlsx`
  - Purpose: Demo/fallback tour data for development
  - Format: XLSX parsed by openpyxl + pandas
  - Location: Project root, loaded if MyRES unavailable
- Session state: Stored in Streamlit's in-memory session state (`st.session_state`)

**Caching:**
- Streamlit built-in caching:
  - `@st.cache_data(ttl=86400)` - Station lookups (24 hours)
  - `@st.cache_data(ttl=3600)` - Journey searches (1 hour)
- No Redis or external caching

## Authentication & Identity

**Auth Provider:**
- MyRES: Custom (username/password form)
- DB API: Public/unauthenticated

**Implementation:**
- MyRES login: `MyRESClient.login()` method
  - Sends POST to `https://res.ivv-berlin.de/index.php?action=login`
  - Extracts session cookie from response headers
  - Validates session with `action=logout` or `abmelden` keywords in response
  - Error messages: "Keine Session erhalten" or "Falsche Zugangsdaten"
- No OAuth, JWT, or external identity provider

## Monitoring & Observability

**Error Tracking:**
- None detected - No external error monitoring (Sentry, etc.)

**Logs:**
- Streamlit console output via `st.status()`, `st.sidebar.status()`, `st.expander()` widgets
- Custom log messages displayed in UI: optimization steps and warnings
- File: `fahrtenplaner/optimizer.py` reports progress via callback function (lines 47-50)

## CI/CD & Deployment

**Hosting:**
- Local machine (Windows/macOS/Linux)
- Streamlit server (default: `http://localhost:8501`)
- No cloud deployment (AWS, Heroku, etc.)

**CI Pipeline:**
- None detected - No GitHub Actions, GitLab CI, or similar

## Environment Configuration

**Required env vars:**
- None - All configuration via Streamlit UI sidebar inputs

**Secrets location:**
- MyRES username/password: Entered directly in Streamlit sidebar (no secrets file)
- No `.env` file exists or is referenced
- Credentials are only in-memory during session

## External Data Sources

**Tour Data Sources:**
1. **MyRES 3 (Primary):** DB rail survey tours
   - Endpoint: `/index.php?action=freie-touren` + DataTables AJAX API
   - Data: Tour number, date, departure/arrival times, stations, payment amount, ride count
   - Filtering: By state (Bundesland), date range, tour availability status

2. **Local Excel (Fallback):** `Freie_Touren_BB_MV_01-03_April_2026.xlsx`
   - Used when MyRES unavailable
   - Parsed by `load_tours_from_excel()` in `myres_client.py` (lines 21-37)
   - Column detection: Fuzzy pattern matching on header names (German/English variations)

**Geographic Data:**
- Station names: Resolved via DB API `/locations` endpoint
- No separate geographic database

## Webhooks & Callbacks

**Incoming:**
- None - Application only calls external APIs, does not expose webhooks

**Outgoing:**
- None - Application makes read-only API calls to MyRES and DB

## API Call Flow

**Journey Planning Workflow:**
1. User selects state, date, stations in Streamlit sidebar
2. `MyRESClient.login()` → authenticates with MyRES
3. `MyRESClient.fetch_free_tours()` → DataTables AJAX query for tours
4. `optimize_day()` in `optimizer.py` begins:
   - `batch_lookup_stations()` → DB API `/locations` to resolve all station names to IDs (cached)
   - For each tour: `check_reachability_with_ids()` → DB API `/journeys` to verify connection feasibility
   - Retry logic: +15 min departure if initial journey fails
5. Results displayed in Streamlit UI with journey details from DB API

**Rate Limiting & Caching Strategy:**
- DB API: 80 requests/minute limit enforced by `RateLimiter` class
- Station caching: 24 hours (geo-stable)
- Journey caching: 1 hour (route availability may change)
- MyRES session: Single login per app session (no token refresh)

## Network & Security Notes

**TLS & Certificates:**
- MyRES: Uses `curl -sk` (skip certificate verification) due to WAF blocking Python HTTP clients
  - File: `fahrtenplaner/myres_client.py` line 180
  - Risk: Vulnerable to MITM, but necessary for WAF bypass
- DB API: Standard HTTPS with certificate verification

**API Stability:**
- DB API is stable and well-documented (public, free API)
- MyRES requires session management and may timeout
- Fallback to Excel import if MyRES unavailable

---

*Integration audit: 2025-03-14*
