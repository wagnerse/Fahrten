# Codebase Concerns

**Analysis Date:** 2026-03-14

## Tech Debt

**Broad Exception Handling Without Logging:**
- Issue: Multiple `except Exception` blocks that silently swallow errors without logging or proper context preservation
- Files: `fahrtenplaner/db_client.py:131-133`, `fahrtenplaner/db_client.py:184-185`, `fahrtenplaner/db_client.py:214-215`, `fahrtenplaner/myres_client.py:34-35`, `fahrtenplaner/myres_client.py:365-366`
- Impact: Makes debugging production issues extremely difficult. API failures, data parsing errors, and connection problems disappear silently with no trace
- Fix approach: Add structured logging with context (station names, tour IDs, API endpoints). Log at ERROR level when exceptions occur, include stack traces for investigation

**Shell Command Injection Risk via curl:**
- Issue: Building shell commands in `myres_client.py:175-217` using string concatenation and subprocess with `shell=False` but still vulnerable patterns
- Files: `fahrtenplaner/myres_client.py:175-217`, `fahrtenplaner/myres_client.py:219-229`, `fahrtenplaner/myres_client.py:314-324`
- Impact: While currently using `shell=False` (safe), the approach is fragile. Future changes could introduce injection vulnerabilities. Credentials passed as command-line arguments could leak in process listings
- Fix approach: Replace curl subprocess calls with proper Python httpx client with custom TLS fingerprint handling. If curl is required, use secure parameter arrays and never pass credentials in command line

**Hardcoded Station Name Normalization:**
- Issue: Station matching logic in `db_client.py:236-255` uses hardcoded suffixes and fuzzy matching with 90% threshold
- Files: `fahrtenplaner/db_client.py:236-255`
- Impact: May miss valid station combinations or match incorrect stations. "Rostock Hbf" vs "Rostock" normalization is fragile. Fuzzy matching at 90% could cause false positives with similarly-named stations
- Fix approach: Build a station database mapping normalized names to official IDs from DB API. Use exact matching against canonical forms rather than string manipulation

**Bitmask DP Hardcoded Tour Limit:**
- Issue: Limits optimization to 20 tours due to 2^n memory constraints (`optimizer.py:126-158`)
- Files: `fahrtenplaner/optimizer.py:126-158`
- Impact: Tour reduction uses heuristic slotting (4-hour windows) that may discard optimal combinations. No warning when tours are silently filtered. Large result sets unpredictably pruned
- Fix approach: Implement progress tracking showing which tours were filtered. Consider A* search or branch-and-bound for better pruning. Document the heuristic's impact on solution quality

**No Input Validation on User Times:**
- Issue: Time inputs in `app.py:339-341` accepted without validation for logical consistency
- Files: `fahrtenplaner/app.py:339-341`
- Impact: If latest_return equals or precedes earliest_departure, the timedelta logic at line 355-356 adds a day. But this only partially fixes the problem—no validation that times make sense relative to available tours
- Fix approach: Add explicit validation: earliest_departure must be before latest_return (same or next day). Validate against first tour departure and last tour arrival times

**Inconsistent Error Return Patterns:**
- Issue: Some functions return None, others raise exceptions, some return empty objects
- Files: `fahrtenplaner/db_client.py:196-233` (returns None), `fahrtenplaner/optimizer.py:24-46` (returns DayPlan()), `fahrtenplaner/myres_client.py:262-334` (returns [] or raises)
- Impact: Callers must handle multiple error patterns. Easy to miss error conditions. No consistent way to distinguish "no result" from "error occurred"
- Fix approach: Define explicit error handling: use Result types or exceptions consistently. Document which functions return None vs empty vs raise

**API Rate Limiter Not Streamlit-Aware:**
- Issue: Rate limiter in `db_client.py:24-42` is a singleton in module scope but Streamlit reruns entire script
- Files: `fahrtenplaner/db_client.py:24-46`
- Impact: Each script rerun recreates the rate limiter, resetting its timestamp window. Could allow many more API requests than the 80/min limit when app reruns frequently
- Fix approach: Move rate limiter to session state using `@st.cache_resource` or store state persistently. Track API call counts across reruns

**Tour Pruning Algorithm Undocumented:**
- Issue: Pre-pruning at line `optimizer.py:126-158` reduces tours silently using efficiency slots but doesn't document impact
- Files: `fahrtenplaner/optimizer.py:126-158`
- Impact: Users get suboptimal results without knowing tours were filtered. No feedback on quality loss
- Fix approach: Add results showing original vs filtered tour count and which tours were removed. Show efficiency metric used for pruning

## Known Bugs

**Station Matching Inconsistency:**
- Symptoms: Some station name variations correctly match (e.g., "Rostock" matches "Rostock Hbf"), others fail silently. Manual station lookup sometimes needed for tours that should be reachable
- Files: `fahrtenplaner/db_client.py:236-255`, `fahrtenplaner/optimizer.py:92`, `fahrtenplaner/optimizer.py:188`, `fahrtenplaner/optimizer.py:241`
- Trigger: Variations in station names from different data sources (MyRES exports, DB API). Compound names like "Rostock (Seebad)" vs normalized forms
- Workaround: Use exact official station names from DB API; filter demo data to use consistent names

**Missing DP Parent Tracking for Empty Chain:**
- Symptoms: Rare case where optimization completes but chain is empty, causing rendering errors
- Files: `fahrtenplaner/optimizer.py:320-338`
- Trigger: When `best_last == -1` but code continues (shouldn't happen, but parent array initialization could mask issue)
- Workaround: Check `num_tours == 0` before rendering

**Overnight Tour Duration Calculation Fragility:**
- Symptoms: Tours crossing midnight may miscalculate transfer windows if hour boundary calculation fails
- Files: `fahrtenplaner/models.py:25-35`
- Trigger: Tour departing 23:00, arriving 01:30 next day. Comparison logic assumes correct date handling
- Workaround: Verify dates in demo data are sequential

## Security Considerations

**Credentials Exposed in Subprocess Environment:**
- Risk: MyRES login credentials passed via command-line arguments to curl could appear in process listings
- Files: `fahrtenplaner/myres_client.py:226-227`
- Current mitigation: Running locally on user machine, subprocess not exposed to network
- Recommendations: Replace curl with httpx library; pass credentials in POST body, not CLI args. Add security warning if deployed as shared service

**External API Dependency Without Fallback:**
- Risk: DB transport.rest API unavailability blocks entire application. No offline mode except demo data
- Files: `fahrtenplaner/optimizer.py` (all phase calls), `fahrtenplaner/db_client.py:90-133`
- Current mitigation: Demo Excel file provided as fallback; caching with 1-3600s TTL
- Recommendations: Extend caching for common routes. Implement circuit breaker pattern for API failures. Document API availability assumptions

**No Rate Limit Handling for Hostile Input:**
- Risk: Malicious user could craft requests to exhaust API quota (80 req/min) by selecting many dates/states
- Files: `fahrtenplaner/optimizer.py` (API call phases)
- Current mitigation: Hard limits on phase 2 transfers (n*(n-1) matrix pruned)
- Recommendations: Add user-visible API call estimate before optimization. Implement per-session daily quotas. Show remaining budget

**SQL/Command Injection via Tour Data:**
- Risk: Tour station names from Excel imported without sanitization before use in URL parameters
- Files: `fahrtenplaner/optimizer.py:55-64` (batch_lookup_stations), `fahrtenplaner/db_client.py:100-101`
- Current mitigation: httpx parameterizes requests (safe), but no input validation on string data
- Recommendations: Validate station names match DB API format (alphanumeric + spaces/hyphens). Reject HTML/markup in tour data at import time

## Performance Bottlenecks

**Phase 2 Transfer Matrix O(n²) API Calls:**
- Problem: Checking reachability between every pair of n tours requires n*(n-1) API calls
- Files: `fahrtenplaner/optimizer.py:160-227`
- Cause: Comprehensive reachability checking. Even with pruning (skipped_time, skipped_same), still potentially hundreds of API calls for large datasets
- Improvement path: Implement geometric pruning (if tour A→B impossible, likely A→any_far_right impossible). Cache transfer results by station pairs. Use background job to pre-compute common routes

**Streamlit Full Rerun on Every Interaction:**
- Problem: App reruns entire Python script on button clicks, state changes, etc. Including API initialization
- Files: `fahrtenplaner/app.py:1-399`
- Cause: Streamlit architecture. Even with caching, session state management on rerun is expensive
- Improvement path: Use `@st.cache_resource` for http client. Implement explicit error boundary on tour loading. Consider using Streamlit's fragment API for isolated reruns

**Bitmask DP Memory O(2^n * n):**
- Problem: DP table of size 2^n * n grows exponentially. For n=20, requires ~20MB. Higher n infeasible
- Files: `fahrtenplaner/optimizer.py:272-299`
- Cause: Algorithm choice for global optimization. Necessary for correctness, but limits scalability
- Improvement path: Implement divide-and-conquer by time slots. Use A* search with greedy heuristic for larger instances. Document n=20 limit in UI

**No Index on Tour Lookups:**
- Problem: Linear search for tour by index in multiple places
- Files: `fahrtenplaner/optimizer.py:346, 368, 377` (list comprehensions)
- Cause: Small dataset size (typically <100 tours), so not critical yet
- Improvement path: Create Tour ID → index mapping at start of optimization for O(1) lookups

## Fragile Areas

**Tour Data Parsing From Excel:**
- Files: `fahrtenplaner/myres_client.py:21-136`
- Why fragile: Column detection regex-based (`_detect_columns`). If MyRES export format changes column order or names, parser silently fails. Time parsing allows multiple formats but date parsing rigid. Duration parsing assumes HH:MM format only
- Safe modification: Add schema validation: check detected columns before parsing first row. Log warnings if expected columns missing. Test with multiple MyRES export formats
- Test coverage: Only implicit in `load_tours_from_excel`. No unit tests for format variations. Test with real MyRES exports if available

**DB API Connection Flow:**
- Files: `fahrtenplaner/optimizer.py:52-262`
- Why fragile: Three separate phases (anreise, transfers, rückreise) that must stay synchronized. If one phase encounters API errors mid-execution, state becomes inconsistent. Station lookup failures silently filter tours
- Safe modification: Use transaction-like pattern: validate all stations exist before starting optimization. If any lookup fails, abort early with clear error message
- Test coverage: Integration tested through `app.py` button only. No isolated phase testing

**Station Name Normalization Logic:**
- Files: `fahrtenplaner/db_client.py:236-255`
- Why fragile: Fuzzy matching at 90% threshold could match "Berlin" with "Berliner" by accident. Suffix removal is fragile (what if "Hbf" is part of real name?). No geographic context to validate matches
- Safe modification: Build station whitelist from DB API. Match against official names only. Remove fuzzy matching entirely
- Test coverage: No tests. Hidden inside optimizer flow

**MyRES curl Subprocess Calls:**
- Files: `fahrtenplaner/myres_client.py:175-217, 219-256, 314-324`
- Why fragile: Depends on curl being installed and accessible. WAF detection logic brittle (checks for specific headers/user-agent). Session cookie extraction regex-based, assumes specific format. If curl output format changes or WAF behavior changes, entire MyRES integration breaks
- Safe modification: Implement proper httpx client with custom TLS fingerprinting if needed. Use structured parsing (JSON) instead of regex for session extraction
- Test coverage: Not tested. Only used in live MyRES access path

## Scaling Limits

**API Request Budget:**
- Current capacity: 80 requests/minute (DB transport.rest rate limit)
- Limit: With n=20 tours, phase 2 alone needs ~380 API calls maximum. At 80/min, takes 5 minutes for one optimization
- Scaling path: Cache transfer lookups by station pair. Pre-compute common routes. Implement request batching. Upgrade to higher-tier API if available

**DP Solution Space:**
- Current capacity: 2^20 = ~1M bitmasks, runs in <500ms
- Limit: 2^21 = 2M bitmasks (marginal). 2^25 = 32M infeasible
- Scaling path: Implement A* or branch-and-bound for larger tours. Partition by time windows. Consider column generation for large instances

**Streamlit Session Memory:**
- Current capacity: Tours list + DP tables fit in <100MB for typical session
- Limit: If >500 tours loaded, memory footprint approaches Streamlit resource limits on shared hosting
- Scaling path: Implement server-side session storage. Stream optimization results incrementally. Implement pagination for tour display

## Dependencies at Risk

**httpx (0.25.0+):**
- Risk: Used for DB API calls. No custom proxy/TLS support specified. Could break if httpx API changes
- Impact: API communication fails completely
- Migration plan: Maintain version pin. If httpx breaks, fallback to requests library (more stable API). Monitor security advisories

**beautifulsoup4 (4.12.0):**
- Risk: Imported but not used in current code (leftover from earlier HTML parsing)
- Impact: Unnecessary dependency, increases attack surface
- Migration plan: Remove from requirements.txt. If HTML parsing needed later, add it back with specific use case

**pandas (2.0.0+):**
- Risk: Large dependency tree. Breaking changes between minor versions possible
- Impact: Excel import fails; entire tour loading broken
- Migration plan: Implement custom Excel parser if possible. Or pin pandas more conservatively. Monitor release notes

**openpyxl (3.1.0):**
- Risk: Only used in `create_excel.py` (demo data generation), not in main app
- Impact: Demo data generation fails, but app still works with live MyRES
- Migration plan: Move to separate requirements file or remove if demo data pre-generated

## Missing Critical Features

**No Optimization Audit Trail:**
- Problem: No saved history of optimizations. If user gets result, closes browser, loses it forever. No way to compare optimization runs
- Blocks: Users cannot iterate or A/B test different parameters

**No Tour Favoriting/Pinning:**
- Problem: Users cannot mark preferred tours to ensure they're included
- Blocks: Cannot optimize for user preferences beyond raw euros/hours

**No Undo/Revert:**
- Problem: If optimization result is poor, no way to revert to previous state without re-running
- Blocks: Exploratory optimization workflow slow

**No API Monitoring/Alerting:**
- Problem: If DB API fails, user only discovers when optimization hangs
- Blocks: Cannot proactively notify of service issues

## Test Coverage Gaps

**Excel Import Format Variations:**
- What's not tested: Handling of different MyRES export column orders, missing columns, non-standard date formats
- Files: `fahrtenplaner/myres_client.py:21-136`
- Risk: Real MyRES exports might fail silently if format differs from assumptions
- Priority: High - primary data path

**Edge Cases in Station Matching:**
- What's not tested: Compound names ("Stadt XY"), abbreviations, special characters, non-Latin script
- Files: `fahrtenplaner/db_client.py:236-255`
- Risk: Legitimate tours marked unreachable due to station name mismatch
- Priority: High - core optimization requirement

**Overnight Tour Transitions:**
- What's not tested: Tours crossing 00:00 boundary with transfers. Multi-day optimization scenarios
- Files: `fahrtenplaner/models.py:25-35`, `fahrtenplaner/optimizer.py:409-410`
- Risk: Miscalculation of transfer windows, false negatives on reachability
- Priority: Medium - affects weekend trips

**API Failure Scenarios:**
- What's not tested: Rate limit exceeded, timeout, 5xx errors, malformed responses, partial data
- Files: `fahrtenplaner/db_client.py:90-133`, `fahrtenplaner/optimizer.py:52-262`
- Risk: Application hangs or crashes instead of gracefully degrading
- Priority: High - production resilience

**Bitmask DP Correctness:**
- What's not tested: Complex transfer chains, ties in optimization value, parent pointer reconstruction
- Files: `fahrtenplaner/optimizer.py:264-338`
- Risk: Suboptimal or incorrect results for complex scenarios
- Priority: Medium - optimization correctness

**Empty/Malformed Tour Lists:**
- What's not tested: Empty dataset, single tour, all tours unreachable from home
- Files: `fahrtenplaner/optimizer.py:43-46`, `fahrtenplaner/app.py:381-387`
- Risk: Cryptic error messages or unexpected behavior
- Priority: Medium - user experience

---

*Concerns audit: 2026-03-14*
