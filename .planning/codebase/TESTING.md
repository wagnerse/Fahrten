# Testing Patterns

**Analysis Date:** 2026-03-14

## Test Framework

**Runner:**
- No test framework detected
- No pytest, unittest, or vitest configuration found
- No test files in codebase

**Assertion Library:**
- Not applicable - no testing framework in use

**Run Commands:**
- No test command defined in project configuration
- No `pytest.ini`, `setup.py`, or test runner configuration detected

## Test File Organization

**Location:**
- No test directory structure present
- No `tests/` or `test/` directories found
- No `*_test.py` or `test_*.py` files detected

**Naming:**
- Not applicable - no tests exist

**Structure:**
- Not applicable - no tests exist

## Test Structure

**Suite Organization:**
- No tests currently implemented in codebase

**Patterns:**
- No setup/teardown patterns used
- No test fixtures present
- No assertion patterns established

## Mocking

**Framework:**
- Not applicable - no testing framework in place

**Patterns:**
- No mocking library detected
- No mock fixtures or stubs observed

**What to Mock:**
- External API calls would be primary candidates:
  - `db_client.py` - `httpx.Client` for DB transport.rest API calls
  - `db_client.py` - `_rate_limiter` behavior for rate limiting tests
  - `myres_client.py` - MyRES HTTP client via curl subprocess calls
  - `myres_client.py` - `pd.read_excel()` for Excel import testing

**What NOT to Mock:**
- Core data structures (Tour, Connection, DayPlan dataclasses)
- Optimizer algorithm logic (`optimize_day()` - should test with real data)
- Helper functions for normalization and parsing

## Fixtures and Test Data

**Test Data:**
- Demo Excel file exists: `Freie_Touren_BB_MV_01-03_April_2026.xlsx`
- Used in app.py for development/fallback: `load_tours_from_excel(_DEMO_EXCEL)`
- Tour data structure documented in `create_excel.py` with 104 sample tours

**Example fixture structure (recommended implementation):**
```python
@pytest.fixture
def sample_tour():
    return Tour(
        tour_nr=704213,
        priority=1,
        day_name="Mi",
        date=date(2026, 4, 1),
        departure_time=time(3, 53),
        departure_station="Lübbenau(Spreewald)",
        arrival_time=time(6, 53),
        arrival_station="Dessau Hbf",
        num_rides=1,
        points=6,
        duration=timedelta(hours=3),
        euros=57.00,
    )
```

**Location:**
- Would be in `tests/fixtures.py` or `tests/conftest.py` if tests were implemented
- Demo Excel can serve as integration test data source

## Coverage

**Requirements:**
- No coverage requirements enforced
- No `.coveragerc` or coverage configuration present
- No CI/CD pipeline detected

**View Coverage:**
- Not applicable - no testing setup

## Test Types

**Unit Tests:**
- Would test individual functions:
  - `models.py` - Property calculations (duration_str, arrival_dt with overnight logic)
  - `db_client.py` - `_stations_match()` string normalization and fuzzy matching
  - `myres_client.py` - Parsing functions (`_parse_time()`, `_parse_date()`, `_parse_duration()`)
  - `optimizer.py` - Transfer time validation (`_check_transfer_warning()`)

**Integration Tests:**
- Would test API layers:
  - DB API station lookup: `batch_lookup_stations()` with real/mocked API
  - Connection search: `find_connection()` with DB transport.rest responses
  - Excel import: `load_tours_from_excel()` with sample Excel files
  - Optimizer with real reachability checks

**E2E Tests:**
- Framework: Not used (no Playwright or similar detected)
- Would ideally test:
  - Streamlit app workflows (MyRES login → tour load → optimization)
  - Browser interaction with web UI
  - Could use Streamlit testing library or Playwright for browser automation

## Common Patterns

**Async Testing:**
- Not applicable - no async code in project

**Error Testing:**
- Would test graceful degradation:
  - API failures: `httpx.HTTPStatusError` handling in `find_connection()`
  - Missing stations: `lookup_station()` returning None
  - Invalid Excel data: `_row_to_tour()` returning None for malformed rows
  - Rate limiting: `RateLimiter` blocking and timing behavior

**Example error test pattern (recommended):**
```python
def test_missing_station_lookup():
    """Test that lookup_station returns None for non-existent station."""
    result = lookup_station("NonexistentStation12345")
    assert result is None

def test_unreachable_station_reachability():
    """Test that check_reachability returns None when no path exists."""
    from_id = "8000001"  # Berlin
    to_id = "8096876"    # Stralsund
    earliest = datetime.now()
    latest = datetime.now() + timedelta(hours=12)

    result = check_reachability_with_ids(
        from_id, to_id, earliest, latest
    )
    # May return None if no connection exists
```

**Integration test pattern (recommended):**
```python
def test_optimizer_full_chain():
    """Integration test: load tours → optimize for a day."""
    tours = load_tours_from_excel(
        Path(__file__).parent / "data/sample_tours.xlsx"
    )
    plan = optimize_day(
        tours=tours[:5],
        home_station="Prenzlau",
        dest_station="Prenzlau",
        earliest_departure=datetime(2026, 4, 1, 4, 0),
        latest_return=datetime(2026, 4, 1, 23, 59),
    )
    assert plan.num_tours > 0
    assert plan.total_euros > 0
```

## Current Test State

**No Testing Framework Implemented:**
- Project lacks unit, integration, and E2E tests
- No automated test execution pipeline
- All code validation currently manual or through Streamlit UI interaction

**Risk Areas Without Tests:**
- Optimizer algorithm (bitmask DP) untested
- API error handling not validated
- Excel parsing edge cases not covered
- Overnight tour arrival time logic not tested
- Reachability graph construction (3 phases) not validated
- Rate limiting behavior not verified

**Recommended First Tests (Priority Order):**
1. Parser functions in `myres_client.py` and `db_client.py`
2. Data model properties (Tour, Connection, DayPlan)
3. Optimizer algorithm with known tour combinations
4. Station matching logic with fuzzy matching

---

*Testing analysis: 2026-03-14*
