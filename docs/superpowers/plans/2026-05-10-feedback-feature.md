# Feedback Feature Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **⚠️ Commit policy (overrides plan template).** This codebase has a hard rule from `CLAUDE.md`: **never commit or push without explicit user instruction.** Every "Commit" step in this plan is GATED. When you reach one: stop, summarize the working-tree changes, and **wait** for the user to say "commit". Do not bundle commits across tasks. Do not push. Phrases like "ship it", "next task", "looks good", "finalize" do NOT authorize a commit. Subagents executing tasks must be told explicitly *not* to commit.

**Spec:** `docs/superpowers/specs/2026-05-10-feedback-feature-design.md`

**Goal:** Add an in-app feedback button that creates a GitHub issue with the user's free-text complaint plus the full optimization context (inputs, winner+alternative chains, complete tour list).

**Architecture:** New top-level `feedback_client.py` (pure logic, fully tested) handles JSON payload assembly, Markdown body rendering, and the GitHub Issues API call. New `ui/feedback.py` owns the button, the `@st.dialog`, and the submission lifecycle. Three small additions to existing UI files persist the inputs the dialog needs.

**Tech Stack:** Python 3.12, Streamlit, `requests` (already a transitive dep via `googlemaps`), pytest with mocked HTTP. No new packages.

**Files touched:**
- Create: `fahrtenplaner/feedback_client.py`
- Create: `fahrtenplaner/ui/feedback.py`
- Create: `tests/test_feedback_client.py`
- Modify: `fahrtenplaner/ui/state.py`
- Modify: `fahrtenplaner/ui/sidebar.py`
- Modify: `fahrtenplaner/ui/optimization.py`
- Modify: `CLAUDE.md`

**Testing pattern:** Mocked HTTP, no live API calls. Mirrors `tests/test_transit_client.py` and `tests/test_optimizer.py`. UI module (`ui/feedback.py`) is **not** unit-tested directly, matching the rest of the codebase. The smoke test in Task 13 covers the UI-to-API integration manually.

---

## Task 1: Skeleton + exception classes + test fixtures

**Files:**
- Create: `fahrtenplaner/feedback_client.py`
- Create: `tests/test_feedback_client.py`

This task lays down the file skeleton, the exception hierarchy, and the test fixtures that every later test reuses. We start TDD on the spec's most subtle exception detail: `FeedbackBodyTooLarge.body` carries the rendered body so the dialog's clipboard fallback can still offer it.

- [ ] **Step 1: Create `tests/test_feedback_client.py` with fixtures and the first failing test**

```python
"""Tests for feedback_client — payload, Markdown rendering, GitHub HTTP. Mocked, no live calls."""

import json
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest
import requests

# Make fahrtenplaner importable
sys.path.insert(0, str(Path(__file__).parent.parent / "fahrtenplaner"))

from models import (
    CarLeg, ChainLink, Connection, DayPlan, Leg, OptimizationResult, Tour,
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

DAY = date(2026, 5, 10)
HOME = "Prenzlau"
DEST = "Stralsund"


def make_tour(
    tour_nr: int,
    dep_time: str,
    dep_station: str,
    arr_time: str,
    arr_station: str,
    euros: float,
    num_rides: int = 1,
    points: int = 0,
    day_name: str = "Mi",
    priority: int = 1,
) -> Tour:
    """Compact Tour factory mirroring tests/test_optimizer.py::make_tour."""
    h1, m1 = map(int, dep_time.split(":"))
    h2, m2 = map(int, arr_time.split(":"))
    dt1 = datetime.combine(DAY, time(h1, m1))
    dt2 = datetime.combine(DAY, time(h2, m2))
    if dt2 < dt1:
        dt2 += timedelta(days=1)
    return Tour(
        tour_nr=tour_nr, priority=priority, day_name=day_name, date=DAY,
        departure_time=time(h1, m1), departure_station=dep_station,
        arrival_time=time(h2, m2), arrival_station=arr_station,
        num_rides=num_rides, points=points,
        duration=dt2 - dt1, euros=euros,
    )


def make_connection(dep_station: str, dep_time: str, arr_station: str, arr_time: str,
                    line: str = "RE3", is_replacement: bool = False) -> Connection:
    return Connection(legs=[Leg(
        departure_station=dep_station,
        departure_time=datetime.combine(DAY, time.fromisoformat(dep_time)),
        arrival_station=arr_station,
        arrival_time=datetime.combine(DAY, time.fromisoformat(arr_time)),
        line=line,
        is_replacement_service=is_replacement,
    )])


def make_simple_plan() -> DayPlan:
    """Plan: outbound → tour → transfer → tour → inbound."""
    tour_a = make_tour(101, "08:30", "Berlin Hbf", "09:18", "Lichtenberg", 35.00, num_rides=4, points=12)
    tour_b = make_tour(102, "10:00", "Lichtenberg", "11:45", "Stralsund Hbf", 65.00, num_rides=6, points=20)
    return DayPlan(chain=[
        ChainLink(type="outbound",
                  connection=make_connection(HOME, "06:42", "Berlin Hbf", "08:15")),
        ChainLink(type="tour", tour=tour_a),
        ChainLink(type="transfer",
                  connection=make_connection("Lichtenberg", "09:30", "Lichtenberg", "09:42",
                                             line="S5"),
                  warning=None),
        ChainLink(type="tour", tour=tour_b),
        ChainLink(type="inbound",
                  connection=make_connection("Stralsund Hbf", "11:50", DEST, "11:50")),
    ])


def make_car_plan() -> DayPlan:
    """Plan: car_outbound → tour → car_inbound (single tour, simplest car case)."""
    tour = make_tour(201, "08:30", "Pasewalk", "09:18", "Pasewalk", 28.00)
    return DayPlan(chain=[
        ChainLink(type="car_outbound",
                  car_leg=CarLeg(from_station=HOME, to_station="Pasewalk",
                                 minutes=30, km=25.0, cost=3.12)),
        ChainLink(type="tour", tour=tour),
        ChainLink(type="car_inbound",
                  car_leg=CarLeg(from_station="Pasewalk", to_station=HOME,
                                 minutes=30, km=25.0, cost=3.12)),
    ])


def make_inputs(date_iso: str = "2026-05-10",
                home: str = HOME, dest: str = DEST,
                same_station: bool = False) -> dict:
    return {
        "date": date_iso,
        "home_station": home,
        "dest_station": dest,
        "same_station": same_station,
        "earliest_departure": "04:00",
        "latest_return": "23:59",
        "max_transfer_gap_minutes": 60,
        "max_car_minutes": 30,
        "fuel_consumption": 7.0,
        "fuel_price": 1.79,
        "selected_bundeslaender": ["Brandenburg", "Mecklenburg-Vorpommern"],
    }


FIXED_TS = datetime(2026, 5, 10, 18, 30, 42, tzinfo=timezone(timedelta(hours=2)))


# ---------------------------------------------------------------------------
# Exception classes
# ---------------------------------------------------------------------------

def test_body_too_large_carries_body_for_clipboard_fallback():
    from feedback_client import FeedbackBodyTooLarge
    exc = FeedbackBodyTooLarge("too big", body="# Feedback\n\n...")
    assert exc.body == "# Feedback\n\n..."
    assert "too big" in str(exc)
```

- [ ] **Step 2: Run test, verify it fails**

```
uv run --with pytest pytest tests/test_feedback_client.py -v
```

Expected: `ModuleNotFoundError: No module named 'feedback_client'` (the file doesn't exist yet).

- [ ] **Step 3: Create `fahrtenplaner/feedback_client.py` with skeleton**

```python
"""GitHub-Issues-based feedback submission. No Streamlit imports — fully testable."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Literal, Optional

import requests

from models import (
    CarLeg, ChainLink, Connection, DayPlan, OptimizationResult, Tour,
)


# 4 KB safety margin under GitHub's 65 KB issue-body cap.
_BODY_SIZE_LIMIT = 60_000

_FEEDBACK_TYPE = Literal["better-route", "app-error"]

_TYPE_LABEL_DE: dict[str, str] = {
    "better-route": "nicht optimal",
    "app-error":    "App-Fehler",
}


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class FeedbackError(Exception):
    """Base class for feedback submission failures."""


class FeedbackNetworkError(FeedbackError):
    """No internet, DNS failure, timeout."""


class FeedbackAuthError(FeedbackError):
    """401/403 from GitHub — PAT invalid, expired, or insufficient scope."""


class FeedbackBodyTooLarge(FeedbackError):
    """Rendered issue body would exceed GitHub's body limit.

    Carries the oversized body on `.body` so the dialog can still offer it via
    the clipboard fallback.
    """

    def __init__(self, message: str, body: str) -> None:
        super().__init__(message)
        self.body = body
```

- [ ] **Step 4: Run test, verify it passes**

```
uv run --with pytest pytest tests/test_feedback_client.py -v
```

Expected: 1 passed.

- [ ] **Step 5: Commit (GATED — wait for user instruction)**

If the user says **"commit"**:

```bash
git add fahrtenplaner/feedback_client.py tests/test_feedback_client.py
git commit -m "feat(feedback): scaffold feedback_client with exception classes"
```

Otherwise: stop, report changes, wait.

---

## Task 2: `build_payload`

**Files:**
- Modify: `fahrtenplaner/feedback_client.py`
- Modify: `tests/test_feedback_client.py`

`build_payload` assembles the versioned JSON payload (Section 2 of the spec) from an `OptimizationResult`, the inputs dict, and the day's tour list. It needs to serialize `Tour` (timedelta → minutes), `DayPlan` (chain links of every type), and handle the `alternative is None` case.

- [ ] **Step 1: Add tests for `build_payload`**

Append to `tests/test_feedback_client.py`:

```python
# ---------------------------------------------------------------------------
# build_payload
# ---------------------------------------------------------------------------

def test_build_payload_top_level_keys():
    from feedback_client import build_payload
    plan = OptimizationResult(winner=make_simple_plan(), alternative=None)
    payload = build_payload(
        plan=plan, inputs=make_inputs(), tours=[],
        feedback_type="better-route", feedback_text="not optimal",
        app_version="0.4.2", submitted_at=FIXED_TS,
    )
    assert payload["version"] == 1
    assert payload["submitted_at"] == FIXED_TS.isoformat()
    assert payload["app_version"] == "0.4.2"
    assert payload["feedback"] == {"type": "better-route", "text": "not optimal"}
    assert payload["inputs"] == make_inputs()
    assert "winner" in payload["result"]
    assert "alternative" in payload["result"]
    assert payload["available_tours"] == []


def test_build_payload_alternative_null_when_no_car_plan():
    from feedback_client import build_payload
    plan = OptimizationResult(winner=make_simple_plan(), alternative=None)
    payload = build_payload(
        plan=plan, inputs=make_inputs(), tours=[],
        feedback_type="better-route", feedback_text="x" * 10,
        app_version="0.4.2", submitted_at=FIXED_TS,
    )
    assert payload["result"]["alternative"] is None


def test_build_payload_alternative_populated_when_car_plan():
    from feedback_client import build_payload
    plan = OptimizationResult(winner=make_simple_plan(), alternative=make_car_plan())
    payload = build_payload(
        plan=plan, inputs=make_inputs(), tours=[],
        feedback_type="better-route", feedback_text="x" * 10,
        app_version="0.4.2", submitted_at=FIXED_TS,
    )
    alt = payload["result"]["alternative"]
    assert alt is not None
    assert alt["mode"] == "car"
    assert alt["num_tours"] == 1


def test_build_payload_winner_chain_serialized_in_order():
    from feedback_client import build_payload
    plan = OptimizationResult(winner=make_simple_plan(), alternative=None)
    payload = build_payload(
        plan=plan, inputs=make_inputs(), tours=[],
        feedback_type="better-route", feedback_text="x" * 10,
        app_version="0.4.2", submitted_at=FIXED_TS,
    )
    chain = payload["result"]["winner"]["chain"]
    assert [link["type"] for link in chain] == [
        "outbound", "tour", "transfer", "tour", "inbound",
    ]
    assert chain[1]["tour_nr"] == 101
    assert chain[1]["euros"] == 35.00


def test_build_payload_serializes_tour_timedelta_as_minutes():
    from feedback_client import build_payload
    tour = make_tour(999, "08:00", "A", "09:30", "B", 25.00)  # 90-minute tour
    plan = OptimizationResult(winner=DayPlan(), alternative=None)
    payload = build_payload(
        plan=plan, inputs=make_inputs(), tours=[tour],
        feedback_type="app-error", feedback_text="x" * 10,
        app_version="0.4.2", submitted_at=FIXED_TS,
    )
    assert payload["available_tours"][0]["duration_minutes"] == 90
    assert payload["available_tours"][0]["tour_nr"] == 999
    assert payload["available_tours"][0]["date"] == "2026-05-10"


def test_build_payload_winner_mode_and_totals():
    from feedback_client import build_payload
    plan = OptimizationResult(winner=make_car_plan(), alternative=None)
    payload = build_payload(
        plan=plan, inputs=make_inputs(), tours=[],
        feedback_type="better-route", feedback_text="x" * 10,
        app_version="0.4.2", submitted_at=FIXED_TS,
    )
    winner = payload["result"]["winner"]
    assert winner["mode"] == "car"
    assert winner["gross_euros"] == 28.00
    assert winner["fuel_cost_euros"] == 6.24      # 3.12 outbound + 3.12 inbound
    assert winner["net_euros"] == pytest.approx(21.76)


def test_build_payload_is_json_serializable():
    """Whole payload must be safe for json.dumps — no datetime/timedelta/dataclass leaks."""
    from feedback_client import build_payload
    tour = make_tour(1, "08:00", "A", "09:00", "B", 30.00)
    plan = OptimizationResult(winner=make_simple_plan(), alternative=make_car_plan())
    payload = build_payload(
        plan=plan, inputs=make_inputs(), tours=[tour],
        feedback_type="better-route", feedback_text="x" * 10,
        app_version="0.4.2", submitted_at=FIXED_TS,
    )
    json.dumps(payload)  # raises if any non-serializable value slipped through


def test_build_payload_uses_now_when_submitted_at_omitted():
    from feedback_client import build_payload
    plan = OptimizationResult(winner=make_simple_plan(), alternative=None)
    before = datetime.now().astimezone()
    payload = build_payload(
        plan=plan, inputs=make_inputs(), tours=[],
        feedback_type="better-route", feedback_text="x" * 10,
        app_version="0.4.2",
    )
    after = datetime.now().astimezone()
    submitted = datetime.fromisoformat(payload["submitted_at"])
    assert before <= submitted <= after
```

- [ ] **Step 2: Run tests, verify they fail**

```
uv run --with pytest pytest tests/test_feedback_client.py -v
```

Expected: 7 new tests fail with `ImportError: cannot import name 'build_payload'`.

- [ ] **Step 3: Implement `build_payload` and its serialization helpers**

Append to `fahrtenplaner/feedback_client.py`:

```python
# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------

def _serialize_tour(tour: Tour) -> dict:
    return {
        "tour_nr":           tour.tour_nr,
        "priority":          tour.priority,
        "day_name":          tour.day_name,
        "date":              tour.date.isoformat(),
        "departure_time":    tour.departure_time.strftime("%H:%M"),
        "departure_station": tour.departure_station,
        "arrival_time":      tour.arrival_time.strftime("%H:%M"),
        "arrival_station":   tour.arrival_station,
        "num_rides":         tour.num_rides,
        "points":            tour.points,
        "duration_minutes":  int(tour.duration.total_seconds() // 60),
        "euros":             tour.euros,
    }


def _serialize_chain_link(link: ChainLink) -> dict:
    if link.type == "tour" and link.tour is not None:
        t = link.tour
        return {
            "type":      "tour",
            "tour_nr":   t.tour_nr,
            "day_name":  t.day_name,
            "departure": t.departure_time.strftime("%H:%M"),
            "arrival":   t.arrival_time.strftime("%H:%M"),
            "from":      t.departure_station,
            "to":        t.arrival_station,
            "num_rides": t.num_rides,
            "points":    t.points,
            "euros":     t.euros,
        }
    if link.type in ("outbound", "inbound", "transfer") and link.connection is not None:
        c = link.connection
        d: dict = {
            "type":                    link.type,
            "departure":               c.departure_time.strftime("%H:%M") if c.departure_time else None,
            "arrival":                 c.arrival_time.strftime("%H:%M")   if c.arrival_time   else None,
            "from":                    c.legs[0].departure_station if c.legs else None,
            "to":                      c.legs[-1].arrival_station  if c.legs else None,
            "transfers":               c.transfers,
            "has_replacement_service": c.has_replacement_service,
        }
        if link.type == "transfer":
            d["warning"] = link.warning
        return d
    if link.type in ("car_outbound", "car_inbound") and link.car_leg is not None:
        cl = link.car_leg
        return {
            "type":        link.type,
            "from":        cl.from_station,
            "to":          cl.to_station,
            "minutes":     cl.minutes,
            "km":          cl.km,
            "cost_euros":  cl.cost,
        }
    return {"type": link.type}


def _serialize_plan(plan: DayPlan) -> dict:
    return {
        "mode":            "car" if plan.has_car_legs else "transit",
        "gross_euros":     plan.total_euros,
        "fuel_cost_euros": plan.total_costs,
        "net_euros":       plan.net_euros,
        "num_tours":       plan.num_tours,
        "warnings":        list(plan.warnings),
        "chain":           [_serialize_chain_link(link) for link in plan.chain],
    }


# ---------------------------------------------------------------------------
# Payload assembly
# ---------------------------------------------------------------------------

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
    """Assemble the versioned JSON payload (Section 2 of the design spec)."""
    if submitted_at is None:
        submitted_at = datetime.now().astimezone()

    winner_dict = (
        _serialize_plan(plan.winner) if plan.winner.num_tours > 0 else None
    )
    alt_dict = (
        _serialize_plan(plan.alternative) if plan.has_alternative else None
    )

    return {
        "version":          1,
        "submitted_at":     submitted_at.isoformat(),
        "app_version":      app_version,
        "feedback":         {"type": feedback_type, "text": feedback_text},
        "inputs":           inputs,
        "result":           {"winner": winner_dict, "alternative": alt_dict},
        "available_tours":  [_serialize_tour(t) for t in tours],
    }
```

- [ ] **Step 4: Run tests, verify they pass**

```
uv run --with pytest pytest tests/test_feedback_client.py -v
```

Expected: 8 passed (1 from Task 1 + 7 new).

- [ ] **Step 5: Commit (GATED)**

```bash
git add fahrtenplaner/feedback_client.py tests/test_feedback_client.py
git commit -m "feat(feedback): add build_payload + serialization helpers"
```

---

## Task 3: `render_issue_title`

**Files:**
- Modify: `fahrtenplaner/feedback_client.py`
- Modify: `tests/test_feedback_client.py`

The title format is `Feedback YYYY-MM-DD: <home> → <dest> (<type-suffix-DE>)`. Type suffix is German per the spec's label-mapping table.

- [ ] **Step 1: Add tests for `render_issue_title`**

Append to `tests/test_feedback_client.py`:

```python
# ---------------------------------------------------------------------------
# render_issue_title
# ---------------------------------------------------------------------------

def _make_payload(feedback_type="better-route", **inputs_overrides):
    """Helper: assemble a payload with sensible defaults and override inputs."""
    from feedback_client import build_payload
    inputs = make_inputs(**inputs_overrides)
    plan = OptimizationResult(winner=make_simple_plan(), alternative=None)
    return build_payload(
        plan=plan, inputs=inputs, tours=[],
        feedback_type=feedback_type, feedback_text="x" * 10,
        app_version="0.4.2", submitted_at=FIXED_TS,
    )


def test_render_issue_title_better_route_uses_german_suffix():
    from feedback_client import render_issue_title
    payload = _make_payload(feedback_type="better-route", home="Prenzlau", dest="Stralsund")
    assert render_issue_title(payload) == "Feedback 2026-05-10: Prenzlau → Stralsund (nicht optimal)"


def test_render_issue_title_app_error_uses_german_suffix():
    from feedback_client import render_issue_title
    payload = _make_payload(feedback_type="app-error", home="Prenzlau", dest="Prenzlau")
    assert render_issue_title(payload) == "Feedback 2026-05-10: Prenzlau → Prenzlau (App-Fehler)"


def test_render_issue_title_preserves_umlauts_in_stations():
    from feedback_client import render_issue_title
    payload = _make_payload(home="Lübeck Hbf", dest="Görlitz")
    assert "Lübeck Hbf → Görlitz" in render_issue_title(payload)
```

- [ ] **Step 2: Run tests, verify they fail**

```
uv run --with pytest pytest tests/test_feedback_client.py -v -k render_issue_title
```

Expected: 3 failures with `ImportError: cannot import name 'render_issue_title'`.

- [ ] **Step 3: Implement `render_issue_title`**

Append to `fahrtenplaner/feedback_client.py`:

```python
# ---------------------------------------------------------------------------
# Markdown rendering — title
# ---------------------------------------------------------------------------

def render_issue_title(payload: dict) -> str:
    inputs = payload["inputs"]
    type_label = _TYPE_LABEL_DE[payload["feedback"]["type"]]
    return (
        f"Feedback {inputs['date']}: "
        f"{inputs['home_station']} → {inputs['dest_station']} "
        f"({type_label})"
    )
```

- [ ] **Step 4: Run tests, verify they pass**

```
uv run --with pytest pytest tests/test_feedback_client.py -v
```

Expected: 11 passed (8 prior + 3 new).

- [ ] **Step 5: Commit (GATED)**

```bash
git add fahrtenplaner/feedback_client.py tests/test_feedback_client.py
git commit -m "feat(feedback): add render_issue_title with German type suffix"
```

---

## Task 4: `render_issue_body` + size guard

**Files:**
- Modify: `fahrtenplaner/feedback_client.py`
- Modify: `tests/test_feedback_client.py`

The body has four visible parts (Feedback / Kontext / Gewinner / Alternative) + a `<details>` block with a tour table and the JSON dump. Size guard: raises `FeedbackBodyTooLarge` (with the rendered body attached) when over 60 KB.

The car-leg time derivation cribs from `ui/render.py::_chain_start_dt` / `_chain_end_dt`: car_outbound's "departure" = next link's departure − minutes; car_inbound's "arrival" = prior link's arrival + minutes.

- [ ] **Step 1: Add tests for `render_issue_body`**

Append to `tests/test_feedback_client.py`:

```python
# ---------------------------------------------------------------------------
# render_issue_body
# ---------------------------------------------------------------------------

def test_render_issue_body_includes_feedback_blockquote():
    from feedback_client import render_issue_body
    payload = _make_payload()
    payload["feedback"]["text"] = "Über Mainz wäre die Route besser gewesen."
    body = render_issue_body(payload)
    assert "## Feedback" in body
    assert "> Über Mainz wäre die Route besser gewesen." in body


def test_render_issue_body_includes_kontext_section():
    from feedback_client import render_issue_body
    body = render_issue_body(_make_payload())
    assert "## Kontext" in body
    assert "**Datum:** 2026-05-10" in body
    assert "**Heimat → Ziel:** Prenzlau → Stralsund" in body
    assert "**App-Version:** 0.4.2" in body
    assert "**Bundesländer:** Brandenburg, Mecklenburg-Vorpommern" in body


def test_render_issue_body_visible_chain_summary_outside_details():
    """The numbered chain list must appear before <details> so it's readable
    without expanding the disclosure on a phone."""
    from feedback_client import render_issue_body
    body = render_issue_body(_make_payload())
    details_start = body.index("<details>")
    gewinner_idx = body.index("## Gewinner")
    assert gewinner_idx < details_start
    # First numbered chain item visible
    assert "1. **06:42 → 08:15**" in body


def test_render_issue_body_omits_alternative_when_null():
    from feedback_client import render_issue_body
    body = render_issue_body(_make_payload())
    assert "## Alternative" not in body


def test_render_issue_body_includes_alternative_when_present():
    from feedback_client import build_payload, render_issue_body
    plan = OptimizationResult(winner=make_simple_plan(), alternative=make_car_plan())
    payload = build_payload(
        plan=plan, inputs=make_inputs(), tours=[],
        feedback_type="better-route", feedback_text="x" * 10,
        app_version="0.4.2", submitted_at=FIXED_TS,
    )
    body = render_issue_body(payload)
    assert "## Alternative" in body
    assert "Auto" in body  # mode label


def test_render_issue_body_car_leg_times_derived_from_neighbours():
    """car_outbound's departure = tour's departure − minutes;
       car_inbound's arrival   = tour's arrival   + minutes."""
    from feedback_client import build_payload, render_issue_body
    plan = OptimizationResult(winner=make_car_plan(), alternative=None)
    payload = build_payload(
        plan=plan, inputs=make_inputs(), tours=[],
        feedback_type="better-route", feedback_text="x" * 10,
        app_version="0.4.2", submitted_at=FIXED_TS,
    )
    body = render_issue_body(payload)
    # Tour starts 08:30, car drives 30 min → outbound starts 08:00
    assert "08:00 → 08:30" in body
    # Tour ends 09:18, car drives 30 min → inbound ends 09:48
    assert "09:18 → 09:48" in body


def test_render_issue_body_details_contains_tour_table_and_json():
    from feedback_client import build_payload, render_issue_body
    tour = make_tour(701, "06:15", "Frankfurt Hbf", "07:30", "Mainz Hbf",
                     25.00, num_rides=2, points=8)
    plan = OptimizationResult(winner=make_simple_plan(), alternative=None)
    payload = build_payload(
        plan=plan, inputs=make_inputs(), tours=[tour],
        feedback_type="better-route", feedback_text="x" * 10,
        app_version="0.4.2", submitted_at=FIXED_TS,
    )
    body = render_issue_body(payload)
    assert "<details>" in body and "</details>" in body
    # Tour-table cells present
    assert "| 701 |" in body
    assert "Frankfurt Hbf" in body and "Mainz Hbf" in body
    # JSON fence present
    assert "```json" in body
    # JSON parses
    json_start = body.index("```json") + len("```json")
    json_end = body.index("```", json_start)
    parsed = json.loads(body[json_start:json_end])
    assert parsed["version"] == 1


def test_render_issue_body_under_size_limit_for_typical_day():
    """30-tour day stays under 20 KB."""
    from feedback_client import build_payload, render_issue_body
    tours = [make_tour(i, "06:00", "A", "07:00", "B", 25.00) for i in range(30)]
    plan = OptimizationResult(winner=make_simple_plan(), alternative=None)
    payload = build_payload(
        plan=plan, inputs=make_inputs(), tours=tours,
        feedback_type="better-route", feedback_text="x" * 100,
        app_version="0.4.2", submitted_at=FIXED_TS,
    )
    body = render_issue_body(payload)
    assert len(body.encode("utf-8")) < 20_000


def test_render_issue_body_raises_when_too_large():
    from feedback_client import build_payload, render_issue_body, FeedbackBodyTooLarge
    # 1000 tours × ~150 bytes JSON each ≈ 150 KB — well over the 60 KB cap
    tours = [make_tour(i, "06:00", f"Station_{i}_with_a_long_name", "07:00",
                       f"Other_{i}_with_a_long_name", 25.00) for i in range(1000)]
    plan = OptimizationResult(winner=make_simple_plan(), alternative=None)
    payload = build_payload(
        plan=plan, inputs=make_inputs(), tours=tours,
        feedback_type="better-route", feedback_text="x" * 10,
        app_version="0.4.2", submitted_at=FIXED_TS,
    )
    with pytest.raises(FeedbackBodyTooLarge) as exc_info:
        render_issue_body(payload)
    # Body is preserved on the exception so the dialog can offer it for copy
    assert exc_info.value.body
    assert "## Feedback" in exc_info.value.body
```

- [ ] **Step 2: Run tests, verify they fail**

```
uv run --with pytest pytest tests/test_feedback_client.py -v -k render_issue_body
```

Expected: 9 failures with `ImportError: cannot import name 'render_issue_body'`.

- [ ] **Step 3: Implement `render_issue_body` and Markdown helpers**

Append to `fahrtenplaner/feedback_client.py`:

```python
# ---------------------------------------------------------------------------
# Markdown rendering — body
# ---------------------------------------------------------------------------

def _fmt_eur(amount: float) -> str:
    return f"{amount:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")


def _link_label(link: dict) -> str:
    return {
        "outbound":     "Anreise",
        "inbound":      "Rückreise",
        "transfer":     "Transfer",
        "car_outbound": "Auto-Anfahrt",
        "car_inbound":  "Auto-Rückfahrt",
    }.get(link["type"], "")


def _hhmm_minus(hhmm: str, minutes: int) -> str:
    h, m = map(int, hhmm.split(":"))
    total = h * 60 + m - minutes
    total %= 24 * 60
    return f"{total // 60:02d}:{total % 60:02d}"


def _hhmm_plus(hhmm: str, minutes: int) -> str:
    return _hhmm_minus(hhmm, -minutes)


def _car_link_times(chain: list[dict], idx: int) -> tuple[str, str]:
    """Derive (departure, arrival) for car_outbound/car_inbound by reading
    the adjacent link's timestamp ± drive minutes. Mirrors
    ui/render.py::_chain_start_dt / _chain_end_dt."""
    link = chain[idx]
    minutes = link["minutes"]
    if link["type"] == "car_outbound":
        # departure = next link's departure − minutes;  arrival = next link's departure
        for j in range(idx + 1, len(chain)):
            nxt = chain[j]
            nxt_dep = nxt.get("departure")
            if nxt_dep:
                return _hhmm_minus(nxt_dep, minutes), nxt_dep
        return "—", "—"
    if link["type"] == "car_inbound":
        # arrival = prior link's arrival + minutes;  departure = prior link's arrival
        for j in range(idx - 1, -1, -1):
            prev = chain[j]
            prev_arr = prev.get("arrival")
            if prev_arr:
                return prev_arr, _hhmm_plus(prev_arr, minutes)
        return "—", "—"
    return "—", "—"


def _render_chain_line(chain: list[dict], idx: int) -> str:
    link = chain[idx]
    n = idx + 1
    if link["type"] == "tour":
        return (
            f"{n}. **{link['departure']} → {link['arrival']}** · "
            f"Tour № {link['tour_nr']} · {link['from']} → {link['to']} · "
            f"{_fmt_eur(link['euros'])}"
        )
    if link["type"] in ("outbound", "inbound", "transfer"):
        label = _link_label(link)
        sev = " · 🚌 SEV" if link.get("has_replacement_service") else ""
        return (
            f"{n}. **{link['departure']} → {link['arrival']}** · "
            f"{label} · {link['from']} → {link['to']}{sev}"
        )
    if link["type"] in ("car_outbound", "car_inbound"):
        dep, arr = _car_link_times(chain, idx)
        cost = _fmt_eur(link["cost_euros"])
        return (
            f"{n}. **{dep} → {arr}** · {_link_label(link)} · "
            f"{link['from']} → {link['to']} ({link['minutes']} min, "
            f"{link['km']:.0f} km, {cost})"
        )
    return f"{n}. {link['type']}"


def _render_plan_md(label: str, plan_dict: dict) -> str:
    mode_word = "Auto" if plan_dict["mode"] == "car" else "Verbindung"
    head = f"## {label} — {mode_word}, {_fmt_eur(plan_dict['net_euros'])} netto"
    lines = [
        _render_chain_line(plan_dict["chain"], i)
        for i in range(len(plan_dict["chain"]))
    ]
    return head + "\n" + "\n".join(lines)


def _render_kontext(payload: dict) -> str:
    inp = payload["inputs"]
    bl = ", ".join(inp.get("selected_bundeslaender", [])) or "—"
    params = (
        f"Abfahrt ab {inp['earliest_departure']} · "
        f"Rückkehr bis {inp['latest_return']} · "
        f"max. Pause {inp['max_transfer_gap_minutes']} Min · "
        f"max. Auto-Anfahrt {inp['max_car_minutes']} Min"
    )
    return (
        "## Kontext\n"
        f"- **Datum:** {inp['date']}\n"
        f"- **Heimat → Ziel:** {inp['home_station']} → {inp['dest_station']}\n"
        f"- **App-Version:** {payload['app_version']}\n"
        f"- **Bundesländer:** {bl}\n"
        f"- **Parameter:** {params}"
    )


def _render_tour_table(tours: list[dict]) -> str:
    if not tours:
        return ""
    rows = [
        "| Tour-Nr | Tag | Ab | Von | An | Nach | Fahrten | Punkte | € |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for t in tours:
        rows.append(
            f"| {t['tour_nr']} | {t['day_name']} | {t['departure_time']} | "
            f"{t['departure_station']} | {t['arrival_time']} | {t['arrival_station']} | "
            f"{t['num_rides']} | {t['points']} | {_fmt_eur(t['euros'])} |"
        )
    return "\n".join(rows)


def _render_details(payload: dict) -> str:
    tours = payload["available_tours"]
    table = _render_tour_table(tours)
    json_dump = json.dumps(payload, indent=2, ensure_ascii=False)
    summary = f"📋 Vollständige Tagestouren ({len(tours)}) + Roh-JSON"
    inner = (
        (table + "\n\n" if table else "")
        + "```json\n" + json_dump + "\n```"
    )
    return f"<details><summary>{summary}</summary>\n\n{inner}\n\n</details>"


def render_issue_body(payload: dict) -> str:
    """Render the full Markdown body (Section 2 of the spec).

    Raises FeedbackBodyTooLarge if the result would exceed _BODY_SIZE_LIMIT,
    carrying the oversized body on the exception so callers can still offer
    it for clipboard copy.
    """
    parts: list[str] = [
        "## Feedback\n> " + payload["feedback"]["text"].replace("\n", "\n> "),
        _render_kontext(payload),
    ]
    winner = payload["result"]["winner"]
    if winner is not None:
        parts.append(_render_plan_md("Gewinner", winner))
    alt = payload["result"]["alternative"]
    if alt is not None:
        parts.append(_render_plan_md("Alternative", alt))
    parts.append(_render_details(payload))

    body = "\n\n".join(parts)
    if len(body.encode("utf-8")) > _BODY_SIZE_LIMIT:
        raise FeedbackBodyTooLarge(
            f"Issue body would be {len(body.encode('utf-8')):,} bytes — "
            f"exceeds {_BODY_SIZE_LIMIT:,} byte limit",
            body=body,
        )
    return body
```

- [ ] **Step 4: Run tests, verify they pass**

```
uv run --with pytest pytest tests/test_feedback_client.py -v
```

Expected: 20 passed (11 prior + 9 new).

- [ ] **Step 5: Commit (GATED)**

```bash
git add fahrtenplaner/feedback_client.py tests/test_feedback_client.py
git commit -m "feat(feedback): render Markdown body with size guard"
```

---

## Task 5: `submit_to_github` happy path

**Files:**
- Modify: `fahrtenplaner/feedback_client.py`
- Modify: `tests/test_feedback_client.py`

POST to `https://api.github.com/repos/{repo}/issues` with the right headers and body, return the issue number.

- [ ] **Step 1: Add happy-path tests**

Append to `tests/test_feedback_client.py`:

```python
# ---------------------------------------------------------------------------
# submit_to_github — happy path
# ---------------------------------------------------------------------------

def _mock_response(status_code: int, json_data: Optional[dict] = None,
                   text: str = "") -> MagicMock:
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.text = text or json.dumps(json_data or {})
    return resp


def test_submit_to_github_returns_issue_number_on_201():
    from feedback_client import submit_to_github
    with patch("feedback_client.requests.post") as post:
        post.return_value = _mock_response(201, {"number": 42, "html_url": "..."})
        n = submit_to_github(
            title="t", body="b", labels=["feedback"],
            token="ghp_xxx", repo="o/r",
        )
    assert n == 42


def test_submit_to_github_posts_correct_url_and_payload():
    from feedback_client import submit_to_github
    with patch("feedback_client.requests.post") as post:
        post.return_value = _mock_response(201, {"number": 1})
        submit_to_github(
            title="My title", body="My body", labels=["feedback", "better-route"],
            token="ghp_xxx", repo="acme/widgets",
        )
    args, kwargs = post.call_args
    assert args[0] == "https://api.github.com/repos/acme/widgets/issues"
    assert kwargs["json"] == {
        "title": "My title", "body": "My body",
        "labels": ["feedback", "better-route"],
    }
    assert kwargs["headers"]["Authorization"] == "Bearer ghp_xxx"
    assert kwargs["headers"]["Accept"] == "application/vnd.github+json"
    assert kwargs["headers"]["X-GitHub-Api-Version"] == "2022-11-28"
    assert kwargs["timeout"] == 15.0
```

- [ ] **Step 2: Run tests, verify they fail**

```
uv run --with pytest pytest tests/test_feedback_client.py -v -k submit_to_github
```

Expected: 2 failures with `ImportError: cannot import name 'submit_to_github'`.

- [ ] **Step 3: Implement `submit_to_github`**

Append to `fahrtenplaner/feedback_client.py`:

```python
# ---------------------------------------------------------------------------
# GitHub HTTP submission
# ---------------------------------------------------------------------------

_GITHUB_API_BASE = "https://api.github.com"


def submit_to_github(
    *,
    title: str,
    body: str,
    labels: list[str],
    token: str,
    repo: str,
    timeout: float = 15.0,
) -> int:
    """POST a new issue to {repo}'s issues endpoint. Returns the issue number.

    Raises:
        FeedbackAuthError on 401/403.
        FeedbackNetworkError on requests.ConnectionError / requests.Timeout.
        FeedbackError on other 4xx/5xx (with the GitHub error message attached).
    """
    url = f"{_GITHUB_API_BASE}/repos/{repo}/issues"
    headers = {
        "Authorization":        f"Bearer {token}",
        "Accept":               "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    payload = {"title": title, "body": body, "labels": labels}

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
    except requests.Timeout as exc:
        raise FeedbackNetworkError(f"GitHub request timed out after {timeout}s") from exc
    except requests.ConnectionError as exc:
        raise FeedbackNetworkError(f"Could not reach GitHub: {exc}") from exc

    if resp.status_code == 201:
        return int(resp.json()["number"])
    if resp.status_code in (401, 403):
        raise FeedbackAuthError(
            f"GitHub rejected the PAT (HTTP {resp.status_code}): "
            f"{_extract_error_message(resp)}"
        )
    raise FeedbackError(
        f"GitHub returned HTTP {resp.status_code}: {_extract_error_message(resp)}"
    )


def _extract_error_message(resp: requests.Response) -> str:
    """Best-effort: GitHub returns {'message': '...'} on errors."""
    try:
        return str(resp.json().get("message", resp.text))
    except (ValueError, AttributeError):
        return resp.text
```

- [ ] **Step 4: Run tests, verify they pass**

```
uv run --with pytest pytest tests/test_feedback_client.py -v
```

Expected: 22 passed (20 prior + 2 new).

- [ ] **Step 5: Commit (GATED)**

```bash
git add fahrtenplaner/feedback_client.py tests/test_feedback_client.py
git commit -m "feat(feedback): submit_to_github happy path"
```

---

## Task 6: `submit_to_github` error paths

**Files:**
- Modify: `tests/test_feedback_client.py`

The implementation already raises the right exception subclasses; this task locks in test coverage for each one.

- [ ] **Step 1: Add error-path tests**

Append to `tests/test_feedback_client.py`:

```python
# ---------------------------------------------------------------------------
# submit_to_github — error paths
# ---------------------------------------------------------------------------

def test_submit_to_github_raises_auth_error_on_401():
    from feedback_client import submit_to_github, FeedbackAuthError
    with patch("feedback_client.requests.post") as post:
        post.return_value = _mock_response(401, {"message": "Bad credentials"})
        with pytest.raises(FeedbackAuthError) as exc_info:
            submit_to_github(title="t", body="b", labels=[],
                             token="bad", repo="o/r")
    assert "401" in str(exc_info.value)
    assert "Bad credentials" in str(exc_info.value)


def test_submit_to_github_raises_auth_error_on_403():
    from feedback_client import submit_to_github, FeedbackAuthError
    with patch("feedback_client.requests.post") as post:
        post.return_value = _mock_response(403, {"message": "Resource not accessible"})
        with pytest.raises(FeedbackAuthError):
            submit_to_github(title="t", body="b", labels=[],
                             token="x", repo="o/r")


def test_submit_to_github_raises_network_error_on_timeout():
    from feedback_client import submit_to_github, FeedbackNetworkError
    with patch("feedback_client.requests.post", side_effect=requests.Timeout("slow")):
        with pytest.raises(FeedbackNetworkError) as exc_info:
            submit_to_github(title="t", body="b", labels=[],
                             token="x", repo="o/r", timeout=5.0)
    assert "timed out" in str(exc_info.value).lower()


def test_submit_to_github_raises_network_error_on_connection_error():
    from feedback_client import submit_to_github, FeedbackNetworkError
    with patch("feedback_client.requests.post",
               side_effect=requests.ConnectionError("DNS")):
        with pytest.raises(FeedbackNetworkError):
            submit_to_github(title="t", body="b", labels=[],
                             token="x", repo="o/r")


def test_submit_to_github_raises_generic_error_on_422():
    from feedback_client import submit_to_github, FeedbackError, FeedbackAuthError
    with patch("feedback_client.requests.post") as post:
        post.return_value = _mock_response(422, {"message": "Validation Failed"})
        with pytest.raises(FeedbackError) as exc_info:
            submit_to_github(title="t", body="b", labels=[],
                             token="x", repo="o/r")
    # Generic FeedbackError, not the auth subclass
    assert not isinstance(exc_info.value, FeedbackAuthError)
    assert "422" in str(exc_info.value)
    assert "Validation Failed" in str(exc_info.value)
```

- [ ] **Step 2: Run tests, verify they pass**

```
uv run --with pytest pytest tests/test_feedback_client.py -v
```

Expected: 27 passed (22 prior + 5 new). All pass on first run because the implementation in Task 5 already handles these cases.

- [ ] **Step 3: Commit (GATED)**

```bash
git add tests/test_feedback_client.py
git commit -m "test(feedback): cover submit_to_github error paths"
```

---

## Task 7: Session-state plumbing

**Files:**
- Modify: `fahrtenplaner/ui/state.py`
- Modify: `fahrtenplaner/ui/sidebar.py`
- Modify: `fahrtenplaner/ui/optimization.py`

Three small changes that together make the inputs the dialog needs accessible from session state. No tests (UI / Streamlit-state changes; the existing test suite isn't UI-aware).

- [ ] **Step 1: Add session-state defaults in `ui/state.py`**

Append inside `init_session_state` in `fahrtenplaner/ui/state.py`, after the existing block:

```python
    # Inputs captured at the moment of the last optimization (consumed by the
    # feedback dialog so it can describe what the user actually ran).
    if "last_plan_inputs" not in st.session_state:
        st.session_state.last_plan_inputs = None

    # Selected Bundesländer — persisted so the feedback payload can include them
    # (the sidebar's _render_data_source_panel writes here on every render).
    if "myres_states" not in st.session_state:
        st.session_state.myres_states = []

    # Feedback dialog state. Cleared after a successful submission.
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

- [ ] **Step 2: Persist Bundesländer in `ui/sidebar.py`**

Inside `_render_data_source_panel`, immediately after the `states = st.multiselect(...)` call (currently around `fahrtenplaner/ui/sidebar.py:138-143`):

```python
        states = st.multiselect(
            "Bundesländer",
            _GERMAN_STATES,
            default=["Brandenburg", "Mecklenburg-Vorpommern"],
            help="Welche Länder soll MyRES nach freien Touren absuchen?",
        )
        st.session_state["myres_states"] = states
```

(The single new line is `st.session_state["myres_states"] = states`.)

- [ ] **Step 3: Stash inputs in `ui/optimization.py::_run_optimization`**

In `fahrtenplaner/ui/optimization.py`, immediately after the line `st.session_state.last_plan = result` (currently around line 147):

```python
    progress_bar.empty()
    st.session_state.last_plan = result
    st.session_state.last_plan_log = log_messages
    st.session_state.last_plan_inputs = {
        "date":                     ctx.selected_date.isoformat(),
        "home_station":             ctx.home_station,
        "dest_station":             ctx.dest_station,
        "same_station":             ctx.same_station,
        "earliest_departure":       dep_time.strftime("%H:%M"),
        "latest_return":            ret_time.strftime("%H:%M"),
        "max_transfer_gap_minutes": max_gap_minutes,
        "max_car_minutes":          max_car_minutes,
        "fuel_consumption":         float(st.session_state.fuel_consumption),
        "fuel_price":               float(st.session_state.fuel_price),
        "selected_bundeslaender":   st.session_state.get("myres_states", []),
    }
```

(The new block is the `st.session_state.last_plan_inputs = {...}` assignment.)

- [ ] **Step 4: Smoke-run the app to confirm nothing crashes**

```
./dev.sh
```

Open the app in your browser, load demo data, run an optimization, then in Streamlit's session state inspector (or via `st.write(st.session_state)` temporarily) confirm `last_plan_inputs` is populated and `myres_states` reflects the multiselect. No new UI is visible yet.

- [ ] **Step 5: Run the full test suite to confirm no regressions**

```
uv run --with pytest pytest tests/
```

Expected: 27 from feedback + however many existing tests pass. Nothing failing.

- [ ] **Step 6: Commit (GATED)**

```bash
git add fahrtenplaner/ui/state.py fahrtenplaner/ui/sidebar.py fahrtenplaner/ui/optimization.py
git commit -m "feat(feedback): persist optimization inputs and Bundesländer in session state"
```

---

## Task 8: `ui/feedback.py` — secrets check + trigger button

**Files:**
- Create: `fahrtenplaner/ui/feedback.py`

The button itself, and the early-return guard that hides it when `[github]` secrets are missing or there's nothing to give feedback on. No dialog yet — that comes in Task 9.

- [ ] **Step 1: Create `fahrtenplaner/ui/feedback.py` with the trigger button only**

```python
"""Feedback button + dialog. Owns dialog state via st.session_state.

The dialog opens when the user clicks the trigger; it lets them choose a
feedback type, write a free-text complaint, preview what'll be sent, and
either submit to GitHub Issues or copy the Markdown body to the clipboard.
"""

from __future__ import annotations

from typing import Optional

import streamlit as st

from models import OptimizationResult, Tour
from updater import current_version

from .sidebar import SidebarContext


def _secrets_present() -> bool:
    """True iff [github] block in secrets.toml has both `token` and `repo`."""
    try:
        gh = st.secrets.get("github", {})
    except Exception:
        return False
    return bool(gh.get("token")) and bool(gh.get("repo"))


def render_feedback_button(
    plan: OptimizationResult,
    ctx: SidebarContext,
    tours: list[Tour],
    inputs: Optional[dict],
) -> None:
    """Render the trigger button. Opens the dialog on click.

    Hidden entirely (no-op) when [github] secrets are missing, when the result
    has no winner chain, or when `inputs` is None (defensive — should always
    be set alongside last_plan).
    """
    if not _secrets_present():
        return
    if plan.winner.num_tours == 0:
        return
    if inputs is None:
        return

    if st.button(
        "💬 Feedback zu dieser Route senden",
        use_container_width=True,
        key="feedback_trigger_btn",
    ):
        st.session_state.feedback_dialog_open = True
        st.rerun()

    if st.session_state.get("feedback_dialog_open"):
        _open_dialog(plan, ctx, tours, inputs)


@st.dialog("💬 Feedback zur Route")
def _open_dialog(
    plan: OptimizationResult,
    ctx: SidebarContext,
    tours: list[Tour],
    inputs: dict,
) -> None:
    """Placeholder body — fleshed out in the next task."""
    st.write("Dialog body coming in Task 9.")
    if st.button("Schließen", key="feedback_close_placeholder"):
        st.session_state.feedback_dialog_open = False
        st.rerun()
```

- [ ] **Step 2: Smoke-run the app**

```
./dev.sh
```

Confirm: the button is **not** visible (no `[github]` block in `secrets.toml` yet — the early-return path is exercised). The app should otherwise behave identically.

- [ ] **Step 3: Add a temporary `[github]` block to `.streamlit/secrets.toml`**

For local testing only — never commit:

```toml
[github]
token = "ghp_dummy"
repo  = "owner/repo"
```

Reload the app. The button should now appear under the result. Click it; the placeholder dialog should open with "Dialog body coming in Task 9.", and the Schließen button should close it.

- [ ] **Step 4: Run full test suite to confirm no import-time regressions**

```
uv run --with pytest pytest tests/
```

Expected: same passing count as Task 7.

- [ ] **Step 5: Commit (GATED)**

```bash
git add fahrtenplaner/ui/feedback.py
git commit -m "feat(feedback): add trigger button + dialog skeleton"
```

---

## Task 9: Dialog body — preview + radio + textarea

**Files:**
- Modify: `fahrtenplaner/ui/feedback.py`

Replace the placeholder dialog body with the real one: context strip, feedback-type radio, textarea, expandable preview, action buttons. No submission logic yet — that's Task 10.

- [ ] **Step 1: Replace `_open_dialog` and add the body helpers**

In `fahrtenplaner/ui/feedback.py`, replace the `_open_dialog` placeholder and add the helpers above it:

```python
_TYPE_OPTIONS = {
    "better-route": "Es gab eine bessere Route",
    "app-error":    "App-Fehler",
}


def _fmt_eur(amount: float) -> str:
    """Local copy of ui/render.py::_fmt_eur — small enough to duplicate
    rather than couple modules."""
    return f"{amount:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")


def _close_dialog() -> None:
    """Reset all dialog state. The two pop()s reach forward to keys that
    Task 10's `_handle_submit` introduces — `pop(..., None)` is safe before
    they exist, and putting them here makes Cancel-after-failure behave
    correctly (no stale body shown on the next open)."""
    st.session_state.feedback_dialog_open = False
    st.session_state.feedback_text = ""
    st.session_state.feedback_type = None
    st.session_state.feedback_last_error = None
    st.session_state.feedback_last_issue_number = None
    st.session_state.pop("_feedback_failed_body", None)
    st.session_state.pop("_feedback_oversized_body", None)


def _context_strip(ctx: SidebarContext, plan: OptimizationResult) -> str:
    """Single-line read-only summary at the top of the dialog."""
    date_de = ctx.selected_date.strftime("%d.%m.%Y")
    route = (
        ctx.home_station
        if ctx.same_station
        else f"{ctx.home_station} → {ctx.dest_station}"
    )
    mode = "Auto" if plan.winner.has_car_legs else "Verbindung"
    return f"{date_de} · {route} · {mode} gewonnen mit {_fmt_eur(plan.winner.net_euros)} netto"


def _render_preview(plan: OptimizationResult, tours: list[Tour], inputs: dict) -> None:
    """Read-only preview of what'll be sent. Reassures the user nothing
    private leaves their machine."""
    with st.expander("Was wird mitgesendet?"):
        st.markdown(
            f"- **Datum:** {inputs['date']}\n"
            f"- **Heimat → Ziel:** {inputs['home_station']} → {inputs['dest_station']}\n"
            f"- **Bundesländer:** "
            f"{', '.join(inputs.get('selected_bundeslaender', [])) or '—'}\n"
            f"- **Parameter:** Abfahrt ab {inputs['earliest_departure']} · "
            f"Rückkehr bis {inputs['latest_return']} · "
            f"max. Pause {inputs['max_transfer_gap_minutes']} Min · "
            f"max. Auto-Anfahrt {inputs['max_car_minutes']} Min\n"
            f"- **Anzahl verfügbarer Touren am Tag:** {len(tours)}"
        )
        st.markdown("**Gewinner-Kette:**")
        for link in plan.winner.chain:
            st.markdown(f"- {link.label}")
        if plan.has_alternative:
            st.markdown("**Alternative:**")
            for link in plan.alternative.chain:
                st.markdown(f"- {link.label}")


@st.dialog("💬 Feedback zur Route")
def _open_dialog(
    plan: OptimizationResult,
    ctx: SidebarContext,
    tours: list[Tour],
    inputs: dict,
) -> None:
    # Success state — shown after a successful submission until the user closes.
    if st.session_state.feedback_last_issue_number is not None:
        n = st.session_state.feedback_last_issue_number
        st.success(f"Danke! Dein Feedback ist als Issue #{n} angelegt.")
        if st.button("Schließen", key="feedback_close_success",
                     use_container_width=True):
            _close_dialog()
            st.rerun()
        return

    # Normal compose state.
    st.caption(_context_strip(ctx, plan))

    feedback_type = st.radio(
        "Art des Feedbacks",
        options=list(_TYPE_OPTIONS.keys()),
        format_func=lambda k: _TYPE_OPTIONS[k],
        index=None
        if st.session_state.feedback_type is None
        else list(_TYPE_OPTIONS.keys()).index(st.session_state.feedback_type),
        key="feedback_type_radio",
    )
    st.session_state.feedback_type = feedback_type

    feedback_text = st.text_area(
        "Was war nicht optimal?",
        value=st.session_state.feedback_text,
        placeholder=(
            "z. B. Die Route über Mainz wäre besser gewesen — "
            "Tour 12345 hätte zwischen die anderen gepasst."
        ),
        height=140,
        key="feedback_text_input",
    )
    st.session_state.feedback_text = feedback_text

    _render_preview(plan, tours, inputs)

    if st.session_state.feedback_last_error:
        st.error(st.session_state.feedback_last_error)

    can_submit = (
        feedback_type is not None
        and feedback_text is not None
        and len(feedback_text.strip()) >= 10
    )

    col_a, col_b = st.columns([1, 1])
    with col_a:
        if st.button("Abbrechen", use_container_width=True,
                     key="feedback_cancel_btn"):
            _close_dialog()
            st.rerun()
    with col_b:
        if st.button("📤 Senden", type="primary", use_container_width=True,
                     disabled=not can_submit, key="feedback_submit_btn"):
            # Submission logic added in Task 10.
            st.warning("Senden noch nicht verdrahtet (Task 10).")
```

- [ ] **Step 2: Smoke-run the app**

```
./dev.sh
```

Open the dialog. Confirm:
- The context strip reads sensibly (e.g. `"10.05.2026 · Prenzlau → Stralsund · Verbindung gewonnen mit 100,00 € netto"`).
- The radio has both options and starts unselected.
- The textarea is empty, with the placeholder visible.
- The preview expander shows date, stations, parameters, Bundesländer, tour count, chain bullets.
- "📤 Senden" is disabled until a radio is chosen and ≥ 10 chars are typed.
- "Abbrechen" closes the dialog and clears the textarea.

- [ ] **Step 3: Run the full test suite**

```
uv run --with pytest pytest tests/
```

Expected: same passing count as Task 7. (No new tests in this task — UI.)

- [ ] **Step 4: Commit (GATED)**

```bash
git add fahrtenplaner/ui/feedback.py
git commit -m "feat(feedback): dialog body — type radio, textarea, preview"
```

---

## Task 10: Submit lifecycle — success / error / clipboard

**Files:**
- Modify: `fahrtenplaner/ui/feedback.py`

Wire the Senden button to `feedback_client`: build payload → render body → submit. Handle each error class with a tailored German message and the `st.code` clipboard fallback. Preserve the textarea contents on failure.

- [ ] **Step 1: Add submission handler and error rendering, replace the Senden stub**

In `fahrtenplaner/ui/feedback.py`:

(a) Add an `import` for the feedback client at the top:

```python
from feedback_client import (
    FeedbackAuthError, FeedbackBodyTooLarge, FeedbackError, FeedbackNetworkError,
    build_payload, render_issue_body, render_issue_title, submit_to_github,
)
```

(b) Add the submission handler and clipboard helper before `_open_dialog`:

```python
def _clipboard_block(body: str) -> None:
    """Show the Markdown body inside st.code so Streamlit's clipboard icon
    appears next to it. Same UX pattern as ui/errors.py."""
    st.caption(
        "Kopiere die Daten und schick sie per E-Mail an den Maintainer."
    )
    st.code(body, language="markdown")


def _build_labels(feedback_type: str) -> list[str]:
    return ["feedback", feedback_type]


def _handle_submit(
    plan: OptimizationResult, tours: list[Tour], inputs: dict,
) -> None:
    """Build payload, render body+title, POST. On success, set
    feedback_last_issue_number; on failure, set feedback_last_error and keep
    the dialog open so the user can retry or copy."""
    feedback_type = st.session_state.feedback_type
    feedback_text = (st.session_state.feedback_text or "").strip()

    payload = build_payload(
        plan=plan, inputs=inputs, tours=tours,
        feedback_type=feedback_type, feedback_text=feedback_text,
        app_version=current_version(),
    )
    title = render_issue_title(payload)

    # Render body up-front so we can offer it for clipboard if it's too large.
    try:
        body = render_issue_body(payload)
    except FeedbackBodyTooLarge as exc:
        st.session_state.feedback_last_error = (
            "Tagesplan zu groß zum direkten Senden — bitte Daten kopieren "
            "und manuell schicken."
        )
        st.session_state["_feedback_oversized_body"] = exc.body
        return

    gh = st.secrets["github"]
    try:
        with st.spinner("Sende Feedback…"):
            issue_number = submit_to_github(
                title=title, body=body,
                labels=_build_labels(feedback_type),
                token=gh["token"], repo=gh["repo"],
            )
    except FeedbackAuthError:
        st.session_state.feedback_last_error = (
            "GitHub-Zugang ungültig. Bitte den Maintainer benachrichtigen — "
            "oder Daten kopieren und per E-Mail schicken."
        )
        st.session_state["_feedback_failed_body"] = body
        return
    except FeedbackNetworkError:
        st.session_state.feedback_last_error = (
            "Keine Verbindung zu GitHub. Probier es später nochmal — oder "
            "kopiere die Daten und schick sie per E-Mail."
        )
        st.session_state["_feedback_failed_body"] = body
        return
    except FeedbackError as exc:
        st.session_state.feedback_last_error = (
            f"GitHub hat das Feedback abgelehnt: {exc}"
        )
        st.session_state["_feedback_failed_body"] = body
        return

    # Success.
    st.session_state.feedback_last_issue_number = issue_number
    st.session_state.feedback_last_error = None
    st.session_state.pop("_feedback_failed_body", None)
    st.session_state.pop("_feedback_oversized_body", None)
```

(c) Replace the Senden stub at the bottom of `_open_dialog`:

```python
    with col_b:
        if st.button("📤 Senden", type="primary", use_container_width=True,
                     disabled=not can_submit, key="feedback_submit_btn"):
            _handle_submit(plan, tours, inputs)
            st.rerun()
```

(d) Replace Task 9's two-line error block inside `_open_dialog`:

```python
    if st.session_state.feedback_last_error:
        st.error(st.session_state.feedback_last_error)
```

with the expanded version that also renders the clipboard fallback when a
preserved body is available:

```python
    if st.session_state.feedback_last_error:
        st.error(st.session_state.feedback_last_error)
        oversized = st.session_state.get("_feedback_oversized_body")
        failed = st.session_state.get("_feedback_failed_body")
        if oversized:
            _clipboard_block(oversized)
        elif failed:
            _clipboard_block(failed)
```

- [ ] **Step 2: Smoke-run the happy path with a real PAT**

Replace the dummy `[github]` entries in `.streamlit/secrets.toml` with a real fine-grained PAT scoped to `issues: write` on a throwaway repo:

```toml
[github]
token = "github_pat_REAL"
repo  = "your-handle/sandbox-repo"
```

Run the app, optimize, click 💬 Feedback, fill out the form, click 📤 Senden. Verify:
- Spinner shows during submission.
- Dialog swaps to "Danke! Dein Feedback ist als Issue #N angelegt."
- The issue actually exists on the GitHub repo with the expected title, body, and labels (`feedback` + `better-route` or `app-error`).

- [ ] **Step 3: Smoke-test the auth-error path**

Edit the PAT to be invalid (e.g. swap a character). Submit again. Confirm:
- Red error: "GitHub-Zugang ungültig. Bitte den Maintainer benachrichtigen — oder Daten kopieren und per E-Mail schicken."
- A code block with the Markdown body appears below, with Streamlit's clipboard icon in the corner.
- The textarea contents are preserved (you don't have to re-type).
- Restoring the valid PAT and clicking Senden again works.

- [ ] **Step 4: Smoke-test the network-error path**

Disconnect from the internet (or set `repo = "nonexistent.invalid/foo"` to force a DNS-style failure on some setups). Submit. Confirm:
- Red error mentions "Keine Verbindung zu GitHub".
- Clipboard fallback appears.
- Reconnecting and submitting works.

- [ ] **Step 5: Run the full test suite**

```
uv run --with pytest pytest tests/
```

Expected: 27 passed (no UI tests added).

- [ ] **Step 6: Commit (GATED)**

```bash
git add fahrtenplaner/ui/feedback.py
git commit -m "feat(feedback): wire submit lifecycle with clipboard fallback"
```

---

## Task 11: Wire the button into the result section

**Files:**
- Modify: `fahrtenplaner/ui/optimization.py`

Add `render_feedback_button(...)` to the bottom of `render_optimization_section` — only on the "winner has chain" branch (no point soliciting feedback when there's nothing to give feedback on).

- [ ] **Step 1: Add the import**

At the top of `fahrtenplaner/ui/optimization.py`, after the existing `from .render import render_result`:

```python
from .feedback import render_feedback_button
from .render import render_result
```

- [ ] **Step 2: Call the button in the result branch**

In `render_optimization_section`, replace the existing `else` branch (currently `lines 315-317`):

```python
    else:
        render_result(result)
        _render_optimization_log()
        render_feedback_button(
            result, ctx, tours,
            inputs=st.session_state.get("last_plan_inputs"),
        )
```

- [ ] **Step 3: Smoke-run the full flow**

```
./dev.sh
```

With a real `[github]` block in `secrets.toml`:
- Load demo data, run optimization. The button "💬 Feedback zu dieser Route senden" appears under the result.
- Click it, fill out the dialog, submit. Issue created.

Without `[github]` (delete the block, reload):
- The button is **not** rendered. The rest of the app behaves normally.

With `[github]` present but no optimization yet:
- The button is **not** rendered (no result to give feedback on).

- [ ] **Step 4: Run the full test suite**

```
uv run --with pytest pytest tests/
```

Expected: 27 passed.

- [ ] **Step 5: Commit (GATED)**

```bash
git add fahrtenplaner/ui/optimization.py
git commit -m "feat(feedback): show feedback button under result"
```

---

## Task 12: CLAUDE.md update

**Files:**
- Modify: `CLAUDE.md`

Document the new `[github]` config block and the two new modules in the existing Configuration / "Files to know" sections, matching the surrounding style.

- [ ] **Step 1: Update the Configuration section**

In `CLAUDE.md`, find the existing Configuration block (currently lists `GOOGLE_MAPS_API_KEY`, `[myres]`, `[auth]`). Append a new bullet:

```markdown
- `[github]` block in `secrets.toml` (optional) — enables in-app feedback. When
  present, a "💬 Feedback zu dieser Route senden" button appears under each
  optimization result; submissions become GitHub issues in the configured repo.
  Required keys: `token` (fine-grained PAT scoped to `issues: write` on `repo`)
  and `repo` (`owner/name`). If absent or incomplete, the feedback button is
  hidden. See `fahrtenplaner/feedback_client.py` for the wire format.
```

- [ ] **Step 2: Update "Files to know" — Business logic**

Find the "Business logic" bullet list under "Files to know". After the line for `myres_client.py`, add:

```markdown
- `fahrtenplaner/feedback_client.py` — Builds the JSON payload for in-app
  feedback, renders the Markdown issue body (with a 60 KB size guard that
  raises `FeedbackBodyTooLarge` carrying the rendered text for the clipboard
  fallback), and POSTs to GitHub via `requests`. Pure logic, no Streamlit.
  Exception hierarchy: `FeedbackError` (base) → `FeedbackNetworkError` /
  `FeedbackAuthError` / `FeedbackBodyTooLarge`.
```

- [ ] **Step 3: Update "Files to know" — UI**

Find the "UI (lives in `ui/`...)" bullet list. After the line for `ui/optimization.py`, add:

```markdown
- `ui/feedback.py` — entry: `render_feedback_button(plan, ctx, tours, inputs)`.
  Renders the trigger button under the optimization result; opens a `@st.dialog`
  with type radio + textarea + preview + send/cancel. On submit, builds the
  payload via `feedback_client.build_payload`, posts via `submit_to_github`,
  swaps to a success state on 201 or to an error block + `st.code` clipboard
  fallback on failure. No-ops when `[github]` secrets are missing or there is
  no winner chain.
```

- [ ] **Step 4: Verify by reading the file back**

Open `CLAUDE.md`, confirm the three insertions read naturally in context and don't duplicate or contradict existing prose.

- [ ] **Step 5: Commit (GATED)**

```bash
git add CLAUDE.md
git commit -m "docs(claude-md): document feedback module and [github] config"
```

---

## Task 13: Manual end-to-end smoke test

**Files:** None (verification only)

A full walk-through with a real PAT against a throwaway repo before declaring done. Acts as the "did this actually work for the dad-as-user case" sanity check.

- [ ] **Step 1: Confirm `.streamlit/secrets.toml` has a real fine-grained PAT**

Scoped to `issues: write` on a sandbox repo — **not** your real Fahrten repo (you don't want sandbox issues polluting real triage).

- [ ] **Step 2: Walk the happy path**

1. Start the app: `./dev.sh`
2. Load demo data via the sidebar's "Demo-Daten laden" button.
3. Run an optimization that produces both a winner and an alternative (set `Auto-Anfahrt` to ≥ 30 min if needed).
4. Click "💬 Feedback zu dieser Route senden".
5. Pick a feedback type, type ~50 chars of text.
6. Expand "Was wird mitgesendet?" — confirm preview lists date, stations, params, Bundesländer, tour count, both chains' link labels.
7. Click "📤 Senden". Spinner runs.
8. Dialog shows "Danke! Dein Feedback ist als Issue #N angelegt." Click Schließen.
9. Open the GitHub repo's Issues tab. Confirm:
   - Title: `Feedback YYYY-MM-DD: <home> → <dest> (<type>)`
   - Labels: `feedback` + (`better-route` or `app-error`)
   - Body: visible chain summary readable on a phone, `<details>` block holds the tour table + JSON
   - JSON parses (paste into a JSON validator if you want to be thorough)

- [ ] **Step 3: Walk each error path**

1. Replace token with `"badtoken"` → error block reads "GitHub-Zugang ungültig…", clipboard fallback appears, body in `st.code`.
2. Restore token, set `repo = "owner/does-not-exist"` → 404, generic error message visible, clipboard fallback appears.
3. Restore repo, disconnect the laptop from the internet → "Keine Verbindung zu GitHub…", clipboard fallback appears.
4. Reconnect, restore valid config, click 📤 Senden again — succeeds.

- [ ] **Step 4: Confirm secrets-absent path**

Remove the `[github]` block entirely from `secrets.toml`, restart the app, run an optimization. The "💬 Feedback…" button must not be visible. The rest of the app behaves identically.

- [ ] **Step 5: Run the full test suite one last time**

```
uv run --with pytest pytest tests/
```

Expected: 27 (or whatever the total is including pre-existing tests) passed, 0 failed.

- [ ] **Step 6: Restore the real `[github]` block**

If you swapped to a sandbox repo for testing, restore the maintainer's preferred `repo` value before declaring done.

- [ ] **Step 7: Final commit (GATED — only if anything changed)**

If the smoke test surfaced fixes, stage and commit them following the same gated pattern. Otherwise, no commit; leave the working tree clean.

```bash
git status   # should be clean
```

---

## Summary

When all tasks are complete you'll have:

- 1 new top-level module (`feedback_client.py`) covering payload assembly, Markdown body rendering with size guard, and GitHub Issues HTTP — fully tested with 27 unit tests.
- 1 new UI module (`ui/feedback.py`) owning the button + dialog + submission lifecycle, including a clipboard fallback that re-uses the same Markdown body the API would have received.
- 3 small additions to existing UI files persisting the inputs the dialog needs.
- Updated CLAUDE.md documenting the new `[github]` config block and both new modules.
- Zero new external dependencies (`requests` is already a transitive dep via `googlemaps`).
- Zero changes to existing optimizer / transit-client / MyRES behaviour.
