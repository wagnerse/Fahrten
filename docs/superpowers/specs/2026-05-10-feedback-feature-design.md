# Feedback feature — Design

**Date:** 2026-05-10
**Status:** Approved for implementation
**Audience:** Future implementor (likely Claude Code)

## Summary

After the optimizer produces a result, give the user a single button to send route-quality feedback to the maintainer as a GitHub issue. Submissions carry the user's free-text complaint plus the full machine-readable context (inputs, winner chain, alternative chain, the day's complete tour list) so the maintainer can reproduce the optimization offline. Two feedback types — *"nicht optimal"* and *"App-Fehler"* — become GitHub labels for triage.

## Problem statement

The app is shipped as a Windows desktop binary to a single end user (the maintainer's father). When the optimizer picks a route the user disagrees with — either because a better chain existed or because something in the app misbehaved — there is currently no in-app feedback path. Forwarding screenshots loses the structured context (which tours were available, which params were set, what the optimizer actually returned), making it hard to reproduce or to verify whether the optimizer actually missed an opportunity. We want a one-click feedback path that captures everything needed to re-run the optimization offline.

## Requirements (from clarifying questions)

| Decision | Outcome |
|---|---|
| Delivery channel | **GitHub Issues via fine-grained PAT.** Programmatic POST to the GitHub REST API, no `mailto:` flow. |
| Trigger location | **Single button under the result**, rendered at the bottom of `render_optimization_section` after the result and optimization log. One button per result, not one per plan card. |
| Feedback taxonomy | **Two types only:** *"Es gab eine bessere Route"* (label `better-route`) and *"App-Fehler"* (label `app-error`). Plus a static `feedback` label on every issue. |
| Payload contents | **Inputs + result + full MyRES tour list** (see Section 2). JSON dumped inside a `<details>` block; visible Markdown chain summary above it. |
| Recipient repo | Configured via `secrets.toml` `[github] repo = "owner/name"`. |
| Failure behaviour | Show error inline in the dialog with a **📋 Daten kopieren** fallback that puts the entire Markdown body on the clipboard, so the user can paste it into a regular email if GitHub is unreachable. |
| Missing-secret behaviour | If `[github]` block is absent or incomplete, **the feedback button is hidden entirely** — same defensive pattern as the GMaps key check in `transit_client.py::_get_client`. |
| Authentication scope | Fine-grained PAT scoped to `issues: write` on a single repo. Worst-case leak = someone spams that one repo's issues, which the maintainer rotates the PAT to fix. |

## Section 1 — Architecture

The change adds **two new modules** and **modifies four existing files**. No existing behaviour changes.

| Layer | Change |
|---|---|
| `feedback_client.py` *(new, top-level)* | Pure logic, **zero Streamlit imports**. Builds the JSON payload from a result + inputs, renders the Markdown issue body and title, POSTs to GitHub via `requests` (already a transitive dep via `googlemaps` — no new package). Defines `FeedbackError` and three subclasses (`FeedbackNetworkError`, `FeedbackAuthError`, `FeedbackBodyTooLarge`). Fully unit-testable. |
| `ui/feedback.py` *(new)* | Streamlit-aware. `render_feedback_button(plan, ctx, tours, inputs)` renders the trigger button + `@st.dialog` dialog. Owns dialog state via `st.session_state` (textarea contents, in-flight submission, success/error banner). Hides the button when `[github]` secrets are missing. |
| `ui/optimization.py` | (1) Stash optimization inputs to `st.session_state["last_plan_inputs"]` alongside `last_plan` inside `_run_optimization` (so the feedback dialog has access to dep_time/ret_time/max_gap_minutes/max_car_minutes). (2) After `render_result(result)` and `_render_optimization_log()`, call `render_feedback_button(...)` (skipped when the chain is empty). |
| `ui/sidebar.py` | One small persistence add: stash `states` (selected Bundesländer) to `st.session_state["myres_states"]` inside `_render_data_source_panel` so the payload can include them. Currently they are returned and then discarded by `render_sidebar`. |
| `ui/state.py` | Add `setdefault` entries for `last_plan_inputs`, `myres_states`, and the `feedback_*` dialog-state keys (full list in Section 4). |
| `CLAUDE.md` | Add the `[github]` block under "Configuration" alongside the existing `[myres]` and GMaps entries. Add `feedback_client.py` and `ui/feedback.py` to "Files to know". |

### Why this split

- `feedback_client.py` parallels `myres_client.py` and `transit_client.py`: external-API client, no Streamlit, fully test-covered. Same convention CLAUDE.md mandates: *"a new external API → new client module at `fahrtenplaner/<name>_client.py` parallel to `myres_client.py`. Keep Streamlit out of it."*
- `ui/feedback.py` is a new section in the main pane, per CLAUDE.md's "Where to add new code" guide: *"A new section in the main pane → new function in `ui/optimization.py` (or a new module if substantial)."* The dialog has enough internal state (textarea, submission lifecycle, success/error swap) to justify a module.
- The trigger is wired from `render_optimization_section`, **not** `render_result`, because (a) `render_result` is concerned with visualization and would have to grow new arguments (`ctx`, `tours`, `inputs`) it doesn't otherwise need, and (b) the button belongs to the section, not to either plan card individually.

## Section 2 — What gets sent

### Issue title

```
Feedback YYYY-MM-DD: <home> → <dest> (<type-label>)
```

Examples:
- `Feedback 2026-05-10: Prenzlau → Prenzlau (nicht optimal)`
- `Feedback 2026-05-10: Prenzlau → Stralsund (App-Fehler)`

### Labels

- `feedback` — always
- `better-route` *or* `app-error` — exactly one of, based on the radio choice

No version label; the app version goes in the body where it's still searchable but doesn't pollute the label index.

**Label-text mapping.** GitHub labels stay in English (technical identifiers); the user-visible radio options and the title parenthetical stay in German (audience). Single source of truth in `feedback_client.py`:

| Radio option (German) | GitHub label (English) | Title suffix (German) |
|---|---|---|
| Es gab eine bessere Route | `better-route` | `(nicht optimal)` |
| App-Fehler | `app-error` | `(App-Fehler)` |

### Issue body (Markdown)

Designed to be readable on a phone *without* expanding any disclosure. The full tour list and JSON dump live inside `<details>` so they're one click away when actually needed.

```markdown
## Feedback
> [user free-text, blockquoted]

## Kontext
- **Datum:** 2026-05-10
- **Heimat → Ziel:** Prenzlau → Prenzlau
- **App-Version:** 0.4.2
- **Bundesländer:** Brandenburg, Mecklenburg-Vorpommern
- **Parameter:** Abfahrt ab 04:00 · Rückkehr bis 23:59 · max. Pause 60 Min · max. Auto-Anfahrt 30 Min

## Gewinner — Verbindung, 142,50 € netto
1. **06:42 → 08:15** · Anreise · Prenzlau → Berlin Hbf (1 h 33 min)
2. **08:30 → 09:18** · Tour № 17 · Berlin Hbf → Lichtenberg · 35,00 €
3. **09:30 → 09:42** · Transfer · Lichtenberg → Ostkreuz (12 min)
4. … (one entry per ChainLink in chain order)

## Alternative — Auto, 138,00 € netto
1. **05:30 → 06:00** · Auto-Anfahrt · Prenzlau → Pasewalk (30 min, 25 km, 3,12 €)
2. …
*(entire section omitted when there is no alternative)*

<details><summary>📋 Vollständige Tagestouren (147) + Roh-JSON</summary>

| Tour-Nr | Tag | Ab | Von | An | Nach | Fahrten | Punkte | € |
|---|---|---|---|---|---|---|---|---|
| 17 | Mi | 08:30 | Berlin Hbf | 09:18 | Lichtenberg | 4 | 12 | 35,00 € |
| … | … | … | … | … | … | … | … | … |

```json
{ "version": 1, "submitted_at": "...", ... }
```

</details>
```

### JSON schema (the part inside `<details>`)

Versioned dict — bump `version` if the shape ever changes so historical issues remain interpretable.

```python
{
  "version": 1,
  "submitted_at": "2026-05-10T18:30:42+02:00",   # ISO 8601 with TZ
  "app_version": "0.4.2",                         # from updater.current_version()
  "feedback": {
    "type": "better-route" | "app-error",
    "text": "<verbatim user input>"
  },
  "inputs": {
    "date": "2026-05-10",
    "home_station": "Prenzlau",
    "dest_station": "Prenzlau",
    "same_station": true,
    "earliest_departure": "04:00",
    "latest_return": "23:59",
    "max_transfer_gap_minutes": 60,
    "max_car_minutes": 30,
    "fuel_consumption": 7.0,
    "fuel_price": 1.79,
    "selected_bundeslaender": ["Brandenburg", "Mecklenburg-Vorpommern"]
  },
  "result": {
    "winner":      <plan-dict>,
    "alternative": <plan-dict> | null
  },
  "available_tours": [<tour-dict>, ...]
}

<plan-dict> = {
  "mode": "transit" | "car",          # derived from has_car_legs
  "gross_euros": 142.50,
  "fuel_cost_euros": 0.0,
  "net_euros": 142.50,
  "num_tours": 4,
  "warnings": ["...", ...],
  "chain": [
    # one entry per ChainLink in chain order; shape varies by .type
    {"type": "outbound",     "departure": "06:42", "arrival": "08:15",
     "from": "Prenzlau", "to": "Berlin Hbf",
     "transfers": 1, "has_replacement_service": false},
    {"type": "tour",         "tour_nr": 17, "day_name": "Mi",
     "departure": "08:30", "arrival": "09:18",
     "from": "Berlin Hbf", "to": "Lichtenberg",
     "num_rides": 4, "points": 12, "euros": 35.00},
    {"type": "transfer",     "departure": "09:30", "arrival": "09:42",
     "from": "Lichtenberg", "to": "Ostkreuz",
     "transfers": 0, "has_replacement_service": false,
     "warning": null},
    {"type": "inbound",      "departure": "...", "arrival": "...",
     "from": "...", "to": "...",
     "transfers": 0, "has_replacement_service": false},
    {"type": "car_outbound", "from": "Prenzlau", "to": "Pasewalk",
     "minutes": 30, "km": 25.0, "cost_euros": 3.12},
    {"type": "car_inbound",  "from": "Pasewalk", "to": "Prenzlau",
     "minutes": 30, "km": 25.0, "cost_euros": 3.12}
  ]
}

<tour-dict> = {
  "tour_nr": 17, "priority": 2, "day_name": "Mi", "date": "2026-05-10",
  "departure_time": "08:30", "departure_station": "Berlin Hbf",
  "arrival_time": "09:18", "arrival_station": "Lichtenberg",
  "num_rides": 4, "points": 12, "duration_minutes": 48, "euros": 35.00
}
```

Notes:
- `Tour.duration` is a `timedelta` and not directly JSON-serializable — serialize as integer minutes.
- Tour identity is `(tour_nr, date)`; there is no separate `id` field on the dataclass.
- Connection legs are intentionally **not** included per-link (transit-leg granularity is overkill for triage; `from`, `to`, `transfers`, `has_replacement_service` carry the information the maintainer needs). If a specific bug requires leg-level detail later, the schema bumps to v2.
- `car_outbound` / `car_inbound` links carry no `departure` / `arrival` fields in the JSON because the underlying `ChainLink` doesn't store them — the times are derived from the adjacent link (the prior link's arrival or the next link's departure shifted by `minutes`). The Markdown body computes them the same way `ui/render.py::_chain_start_dt` / `_chain_end_dt` already do; `feedback_client` cribs that logic. Keeping the JSON limited to the dataclass-native fields means a re-runnable test fixture stays a verbatim slice of the in-memory state.

### Size sanity

A heavy day (~150 tours) produces ≈ 25 KB of body, well under GitHub's 65 KB issue-body limit. `feedback_client.render_issue_body` checks the rendered length and raises `FeedbackBodyTooLarge` at 60 KB (4 KB safety margin). If it ever fires, the failure path surfaces an error in the dialog — the user can still copy the payload via the **📋 Daten kopieren** fallback. A Gist-overflow path is **not** part of v1 (YAGNI).

### Privacy

Nothing sensitive is sent. The payload contains MyRES tour metadata (which the user already sees in their daily work) plus their typed feedback. **No** MyRES password, **no** Google Maps key, **no** PAT. The `feedback_client` never reads `secrets.toml` for anything but the `[github]` block.

## Section 3 — UX flow

### Trigger

A single button rendered at the bottom of `render_optimization_section`, immediately after `_render_optimization_log()`, only when the optimization actually produced a chain (`result.winner.num_tours > 0`):

```
💬 Feedback zu dieser Route senden
```

The button is hidden — `render_feedback_button` early-returns silently — when:

- `st.secrets["github"]["token"]` or `st.secrets["github"]["repo"]` is missing
- `st.session_state.last_plan` is `None` or has an empty winner chain
- `st.session_state.last_plan_inputs` is `None` (defensive — should always be present alongside `last_plan`)

### Dialog (`@st.dialog("💬 Feedback zur Route")`)

Vertical order:

1. **Context strip** *(read-only, one line)*: `"10.05.2026 · Prenzlau → Prenzlau · Verbindung gewonnen mit 142,50 € netto"`
2. **Art des Feedbacks** *(radio, required)*:
   - `Es gab eine bessere Route` → label `better-route`
   - `App-Fehler` → label `app-error`
3. **Was war nicht optimal?** *(textarea, required, min 10 chars after strip)* — German placeholder hint suggesting a concrete example so the user knows what to write.
4. **Expander "Was wird mitgesendet?"** *(read-only preview)* — shows: inputs (date/stations/params/Bundesländer), winner chain bullet list, alternative chain bullet list (if any), `Anzahl verfügbarer Touren am Tag: N`. Reassures the user nothing private is being sent.
5. **Button row at bottom**: `Abbrechen` *(secondary, closes dialog)* | `📤 Senden` *(primary)*. Senden is disabled until both radio and textarea are populated.

### Submission lifecycle

On Senden click:

| State | UI |
|---|---|
| Submitting | Spinner: *"Sende Feedback…"* — radio + textarea + Senden disabled |
| ✅ Success | Dialog content swaps to: *"Danke! Dein Feedback ist als Issue #42 angelegt."* + a `Schließen` button. Issue number from the API response. (No auto-close — Streamlit's dialog API has no native timer; an explicit close button is more reliable than a `time.sleep + st.rerun` hack.) |
| ❌ Network error | Red error block: *"Keine Verbindung zu GitHub. Probier es später nochmal — oder kopiere die Daten und schick sie per E-Mail."* + **📋 Daten kopieren** button + retry. Textarea preserved. |
| ❌ Auth error | Red error block: *"GitHub-Zugang ungültig. Bitte den Maintainer benachrichtigen."* + **📋 Daten kopieren** button. No retry (won't help). |
| ❌ Body too large | Red error block: *"Tagesplan zu groß zum Senden — bitte Daten kopieren und manuell schicken."* + **📋 Daten kopieren** button. |
| ❌ Other 4xx/5xx | Red error block with the GitHub error message + **📋 Daten kopieren** button + retry. |

The clipboard fallback re-uses the same Markdown body the API would have received. Implementation matches the existing `ui/errors.py` pattern: render the body inside `st.code(markdown_body, language="markdown")`, which Streamlit decorates with a built-in clipboard icon — no custom JS, no third-party copy-to-clipboard component. The user clicks the icon, then pastes into any email client.

## Section 4 — File layout & interfaces

### `fahrtenplaner/feedback_client.py` *(new)*

```python
"""GitHub-Issues-based feedback submission. No Streamlit imports — fully testable."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

import requests

from models import (
    CarLeg, ChainLink, Connection, DayPlan, OptimizationResult, Tour,
)


_FEEDBACK_TYPE = Literal["better-route", "app-error"]
_BODY_SIZE_LIMIT = 60_000  # 4 KB margin under GitHub's 65 KB issue-body cap


class FeedbackError(Exception):
    """Base class for feedback submission failures."""

class FeedbackNetworkError(FeedbackError):
    """No internet, DNS failure, timeout."""

class FeedbackAuthError(FeedbackError):
    """401/403 from GitHub — PAT invalid, expired, or insufficient scope."""

class FeedbackBodyTooLarge(FeedbackError):
    """Rendered issue body would exceed GitHub's body limit.
    Carries the oversized body on `.body` so the dialog can still offer it via
    the clipboard fallback."""
    def __init__(self, message: str, body: str) -> None:
        super().__init__(message)
        self.body = body


def build_payload(
    *,
    plan: OptimizationResult,
    inputs: dict,
    tours: list[Tour],
    feedback_type: _FEEDBACK_TYPE,
    feedback_text: str,
    app_version: str,
    submitted_at: Optional[datetime] = None,
) -> dict:
    """Assemble the versioned JSON payload (the dict from Section 2).
    `submitted_at` defaults to datetime.now(local TZ) — overridable for tests."""


def render_issue_title(payload: dict) -> str:
    """`Feedback YYYY-MM-DD: <home> → <dest> (<type-label>)`"""


def render_issue_body(payload: dict) -> str:
    """Produces the Markdown issue body (visible chain summary + <details> JSON dump).
    Raises FeedbackBodyTooLarge if rendered length exceeds _BODY_SIZE_LIMIT."""


def submit_to_github(
    *, title: str, body: str, labels: list[str],
    token: str, repo: str, timeout: float = 15.0,
) -> int:
    """POST to https://api.github.com/repos/{repo}/issues. Returns issue number.

    Raises:
        FeedbackAuthError on 401/403.
        FeedbackNetworkError on `requests.ConnectionError` / `requests.Timeout`.
        FeedbackError on other 4xx/5xx (with the GitHub error message attached).
    """
```

The two-step (`build_payload` → `render_issue_body`) split exists so the clipboard-copy fallback can render the same Markdown without ever touching the network. A single rendering function = a single source of truth for body content.

`inputs` is a dict (not a dataclass) to keep the client decoupled from the UI's state shape — the UI assembles it once per submission.

### `fahrtenplaner/ui/feedback.py` *(new)*

```python
"""Feedback button + dialog. Owns dialog state via st.session_state."""

from __future__ import annotations

import streamlit as st

from models import OptimizationResult, Tour
from .sidebar import SidebarContext


def render_feedback_button(
    plan: OptimizationResult,
    ctx: SidebarContext,
    tours: list[Tour],
    inputs: dict,
) -> None:
    """Render the trigger button. Opens a @st.dialog on click.

    No-op (returns silently) if [github] secrets are missing or if the result
    has no valid chain.
    """
```

Internal helpers (private to the module): `_secrets_present()`, `_open_dialog(plan, ctx, tours, inputs)`, `_render_dialog_body(...)`, `_handle_submit(...)`, `_render_success(issue_number)`, `_render_error(exc)`, `_clipboard_button(markdown_body)`.

Session-state keys (all prefixed `feedback_`):
- `feedback_dialog_open` — bool, controls dialog visibility
- `feedback_text` — preserved across reruns and submission failures
- `feedback_type` — radio selection
- `feedback_submitting` — bool during in-flight POST
- `feedback_last_error` — Optional[str], shown in the dialog after a failure
- `feedback_last_issue_number` — Optional[int], shown on success

### `fahrtenplaner/ui/optimization.py` *(modified)*

Two changes:

**1. Stash optimization inputs alongside the result** in `_run_optimization`:

```python
# After: st.session_state.last_plan = result
st.session_state.last_plan_inputs = {
    "date": ctx.selected_date.isoformat(),
    "home_station": ctx.home_station,
    "dest_station": ctx.dest_station,
    "same_station": ctx.same_station,
    "earliest_departure": dep_time.strftime("%H:%M"),
    "latest_return": ret_time.strftime("%H:%M"),
    "max_transfer_gap_minutes": max_gap_minutes,
    "max_car_minutes": max_car_minutes,
    "fuel_consumption": float(st.session_state.fuel_consumption),
    "fuel_price": float(st.session_state.fuel_price),
    "selected_bundeslaender": st.session_state.get("myres_states", []),
}
```

**2. Wire the feedback button** at the end of the result branch:

```python
# Inside render_optimization_section, after render_result/log:
elif result.winner.num_tours > 0:
    render_result(result)
    _render_optimization_log()
    render_feedback_button(
        result, ctx, tours,
        inputs=st.session_state.get("last_plan_inputs", {}),
    )
```

### `fahrtenplaner/ui/sidebar.py` *(modified)*

One line in `_render_data_source_panel`, right after the `states = st.multiselect(...)` call:

```python
st.session_state["myres_states"] = states
```

Persists the Bundesländer selection across reruns so the feedback payload can include them. (Currently `states` is returned to `render_sidebar` and discarded.)

### `fahrtenplaner/ui/state.py` *(modified)*

Add the new session-state keys to `init_session_state`, matching the existing
`if "X" not in st.session_state:` style:

```python
if "last_plan_inputs" not in st.session_state:
    st.session_state.last_plan_inputs = None
if "myres_states" not in st.session_state:
    st.session_state.myres_states = []
if "feedback_dialog_open" not in st.session_state:
    st.session_state.feedback_dialog_open = False
if "feedback_text" not in st.session_state:
    st.session_state.feedback_text = ""
if "feedback_type" not in st.session_state:
    st.session_state.feedback_type = None
if "feedback_submitting" not in st.session_state:
    st.session_state.feedback_submitting = False
if "feedback_last_error" not in st.session_state:
    st.session_state.feedback_last_error = None
if "feedback_last_issue_number" not in st.session_state:
    st.session_state.feedback_last_issue_number = None
```

### `secrets.toml`

New optional block:

```toml
[github]
token = "github_pat_..."   # fine-grained PAT, scoped to issues:write on `repo`
repo  = "owner/name"       # e.g. "sebastianpwagner/Fahrten"
```

When this block is missing or incomplete, the feedback button does not render — same defensive pattern as the `GOOGLE_MAPS_API_KEY` check.

## Section 5 — Tests

`tests/test_feedback_client.py` *(new)* — pure unit tests, mocked HTTP, same pattern as `tests/test_transit_client.py`:

| Test | Asserts |
|---|---|
| `test_build_payload_includes_full_tour_list` | `available_tours` length == input tour count; tour fields are present |
| `test_build_payload_alternative_null_when_no_car_mode` | `payload["result"]["alternative"] is None` when `OptimizationResult.alternative is None` |
| `test_build_payload_serializes_timedelta_as_minutes` | `available_tours[0]["duration_minutes"]` is int, equals `tour.duration.total_seconds() // 60` |
| `test_build_payload_includes_app_version` | `payload["app_version"] == "0.4.2"` |
| `test_render_issue_title_format` | matches `r"^Feedback \d{4}-\d{2}-\d{2}: .+ → .+ \((nicht optimal\|App-Fehler)\)$"` |
| `test_render_issue_body_includes_visible_chain_summary` | numbered list with at least one tour entry appears *outside* `<details>` |
| `test_render_issue_body_omits_alternative_section_when_null` | no `## Alternative` heading when `alternative is None` |
| `test_render_issue_body_under_size_limit_for_typical_day` | a 30-tour fixture renders to < 20 KB |
| `test_render_issue_body_raises_when_too_large` | a synthetic 1000-tour fixture raises `FeedbackBodyTooLarge` |
| `test_submit_to_github_posts_correct_request` | URL == `https://api.github.com/repos/{repo}/issues`, body JSON has `title`, `body`, `labels`, headers include `Authorization: Bearer …` and `Accept: application/vnd.github+json` |
| `test_submit_to_github_returns_issue_number` | parses `number` field from the 201 response |
| `test_submit_to_github_raises_auth_error_on_401` | 401 raises `FeedbackAuthError` |
| `test_submit_to_github_raises_auth_error_on_403` | 403 raises `FeedbackAuthError` |
| `test_submit_to_github_raises_network_error_on_timeout` | `requests.Timeout` raises `FeedbackNetworkError` |
| `test_submit_to_github_raises_network_error_on_connection_error` | `requests.ConnectionError` raises `FeedbackNetworkError` |
| `test_submit_to_github_raises_generic_on_422` | 422 raises `FeedbackError` (not a subclass) with the GitHub message in `str(exc)` |

The UI module (`ui/feedback.py`) is **not** directly test-covered, matching the rest of the codebase's testing posture per CLAUDE.md.

## Section 6 — Out of scope (v1)

These are deliberate non-features. Documented so they don't get re-litigated mid-implementation.

| Excluded | Why |
|---|---|
| Attaching a JSON file to the GitHub issue | Issue-body inlining is enough for our payload size; a file upload requires a separate Gist API call and adds failure modes. |
| Multiple recipient configurations | One repo, one PAT. If the maintainer ever wants to route some feedback elsewhere, that's a separate spec. |
| Anonymous proxy / Cloudflare Worker | Single-user app; embedding a scoped PAT is acceptable risk. Move to a proxy if the app ever ships to multiple users. |
| Editing or deleting submitted feedback | Issues are append-only. The maintainer closes / labels in GitHub. |
| In-app issue browsing | Feedback is one-way; the maintainer reads it on GitHub. |
| Screenshot capture | Streamlit can't easily snapshot itself, and the structured payload is more useful than a screenshot anyway. |
| Bundling the optimization log | Already in `st.session_state.last_plan_log`, but it's noisy and the payload's `available_tours` + `inputs` already let the maintainer re-run. Add later if a real bug needs it. |
| Rate limiting / spam guard | Single user; not a concern. GitHub's own rate limits are far above what one human can hit. |

## Implementation order

Suggested for the implementation plan that follows:

1. `feedback_client.py` (with full test coverage) — pure logic, no Streamlit, fastest to verify.
2. Session-state additions in `ui/state.py` and the `myres_states` persistence in `ui/sidebar.py`.
3. `last_plan_inputs` stashing in `ui/optimization.py::_run_optimization`.
4. `ui/feedback.py` (button + dialog + lifecycle).
5. Wire the button from `ui/optimization.py::render_optimization_section`.
6. CLAUDE.md update (Configuration block + Files to know).
7. Smoke test end-to-end with a real PAT against a throwaway repo before merging.
