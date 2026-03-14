# Coding Conventions

**Analysis Date:** 2026-03-14

## Naming Patterns

**Files:**
- Lowercase with underscores: `models.py`, `db_client.py`, `myres_client.py`, `optimizer.py`
- Application entry point: `app.py`

**Functions:**
- snake_case for all functions: `optimize_day()`, `lookup_station()`, `_render_result()`
- Private/internal functions prefixed with single underscore: `_parse_journey()`, `_render_tour_block()`, `_stations_match()`
- Helper functions named descriptively: `batch_lookup_stations()`, `check_reachability_with_ids()`

**Variables:**
- snake_case for local and module-level variables: `can_reach_from_home`, `transfer_pairs`, `skipped_time`
- Constants in UPPER_CASE: `MIN_TRANSFER_MINUTES`, `TIGHT_TRANSFER_MINUTES`, `MAX_TRANSFER_GAP_HOURS`, `BASE_URL`, `FULL`, `NEG_INF`
- Private module variables prefixed with underscore: `_rate_limiter`, `_http_client`, `_DEMO_EXCEL`
- Abbreviations used in performance contexts: `pct` (percent), `msg` (message), `dep` (departure), `arr` (arrival)

**Types:**
- Use of type hints with `from __future__ import annotations` for forward references
- Optional types explicitly marked: `Optional[Tour]`, `Optional[Connection]`, `Optional[str]`
- List types: `list[Tour]`, `list[str]`, `list[dict]`
- Dict types: `dict[str, Optional[dict]]`, `dict[str, str]`
- Callable types used: `Callable[[float, str], None]`

## Code Style

**Formatting:**
- Python 3.13+ (specified in `pyproject.toml` with `requires-python = ">=3.13"`)
- 4-space indentation throughout
- No explicit formatter detected, but code follows clean, readable style
- Line length appears to be ~100 characters (observed in code)

**Linting:**
- No `.pylintrc`, `.flake8`, or similar config files detected
- No linting tool enforced in project configuration
- Code follows implicit PEP 8-like conventions through manual discipline

## Import Organization

**Order:**
1. Future imports: `from __future__ import annotations` (always first)
2. Standard library: `import sys`, `from datetime import ...`, `from pathlib import Path`
3. Third-party libraries: `import pandas as pd`, `import streamlit as st`, `import httpx`
4. Local imports: `from models import Tour`, `from db_client import ...`
5. Import grouping separated by blank lines

**Path Aliases:**
- `sys.path.insert(0, str(Path(__file__).parent))` used in `app.py` for local package imports
- Relative imports used within `fahrtenplaner/` package

**Example from app.py:**
```python
from __future__ import annotations

import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent))
from models import Tour, DayPlan, ChainLink
from myres_client import MyRESClient, load_tours_from_excel
from optimizer import optimize_day
```

## Error Handling

**Patterns:**
- Silent exception handling with `except Exception:` used pragmatically: `pass` when non-critical
- Example in `db_client.py:184-185`: Continues processing when parsing JSON journey leg fails
- Example in `myres_client.py:34-35`: Skips malformed rows during Excel import with `continue`
- Try-except blocks around API calls with graceful degradation: `try/except/else` patterns
- Error context captured in `self._last_error` attributes (MyRESClient pattern)
- HTTP error escalation with `resp.raise_for_status()` when appropriate (`db_client.py:73`)
- Timeout handling: `timeout=15.0` for httpx, `timeout=35` for subprocess calls

**Error Messages:**
- Descriptive messages in Streamlit UI: `st.sidebar.error()`, `st.error()`, `st.warning()`
- Progress callback reporting: `progress_callback(pct, msg)` with user-friendly German messages
- Exception context preserved where important: `self._last_error = str(e)`

## Logging

**Framework:** No structured logging framework (logging module not used)

**Patterns:**
- Streamlit-based progress reporting: `st.status()`, `st.progress()`
- Progress callbacks: Functions accepting `Callable[[float, str], None]` for status updates
- No file-based logging detected
- Console output used in utility scripts: `print()` in `create_excel.py`
- Message accumulation for display: `log_messages: list[str] = []` in `app.py:359`

**Example from optimizer.py:**
```python
def progress_cb(pct: float, msg: str):
    progress_bar.progress(min(pct, 1.0), text=msg)
    log_messages.append(msg)
```

## Comments

**When to Comment:**
- Module docstrings present: `"""Erhebungsfahrten-Planer – Streamlit Web App."""`
- Complex algorithms documented: Section headers with `# ========== Phase 1/3: Anreise ==========`
- Non-obvious logic explained: German comments for domain-specific decisions
- Business rules explained: `# Übernacht-Tour: Ankunft nach Mitternacht`, `# Schienenersatzverkehr erkennen`

**JSDoc/TSDoc:**
- Function docstrings used for key functions in optimizer.py:
```python
def optimize_day(
    tours: list[Tour],
    home_station: str,
    ...
) -> DayPlan:
    """
    Berechnet die optimale Tourenkette für einen Tag.

    Args:
        tours: Verfügbare Touren für diesen Tag
        ...
    """
```
- Docstrings for public API functions, minimal for internal functions
- Parameter documentation included in multi-parameter functions

## Function Design

**Size:**
- Mostly compact, 10-50 lines per function
- Optimizer algorithm ~150 lines but logically compartmentalized with section headers
- Streamlit render functions 20-30 lines with clear responsibilities

**Parameters:**
- Functions generally have 2-6 parameters
- Optional parameters use defaults: `progress_callback: Optional[Callable...] = None`
- Dictionary unpacking used for flexible column mapping: `col_map: dict[str, str]`
- Keyword-only arguments via explicit function parameters (not `*kwargs` pattern)

**Return Values:**
- Explicit return types in annotations: `-> DayPlan`, `-> Optional[Connection]`, `-> list[Tour]`
- None returns used for failure cases: `return None` when lookup fails
- Dataclass returns used: `return DayPlan()` for composite results
- Lists returned for collections: `list[Tour]`, `list[ChainLink]`

## Module Design

**Exports:**
- Public functions defined at module level without `__all__` declarations
- Private functions prefixed with underscore: `_parse_journey()`, `_render_result()`
- Classes used for stateful clients: `RateLimiter`, `MyRESClient`
- Dataclasses for data models: `Tour`, `DayPlan`, `Connection`, `Leg`, `ChainLink`

**Barrel Files:**
- No barrel files (index.py) detected
- Direct imports from specific modules used: `from models import Tour`

**Module-level State:**
- Global objects allowed for stateless utilities: `_rate_limiter = RateLimiter()`, `_http_client = httpx.Client()`
- Streamlit caching decorators for functions: `@st.cache_data(ttl=86400, show_spinner=False)`
- Session state managed via `st.session_state` dictionary

## Language-Specific Patterns

**Type Annotations:**
- Comprehensive use throughout: All function signatures include return types
- Forward reference handling: `from __future__ import annotations` used consistently
- Optional handling: `Optional[T]` for nullable types
- Dataclass usage: `@dataclass` decorator for immutable data structures

**Async/Concurrency:**
- No async/await patterns used
- Subprocess calls for curl: `subprocess.run(cmd, capture_output=True, text=True, timeout=35)`
- Rate limiting with deque: Token bucket rate limiter in `db_client.py:24-42`

**String Handling:**
- f-strings used throughout: `f"{self.total_euros:.2f} €"`
- String formatting with `.format()` and f-strings mixed
- Regex for parsing: `re.match()`, `re.search()` for robust parsing
- String normalization functions: `.lower()`, `.strip()`, `.removesuffix()`

---

*Convention analysis: 2026-03-14*
