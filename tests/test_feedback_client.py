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


# ---------------------------------------------------------------------------
# Coverage: transfer warnings and SEV propagation
# ---------------------------------------------------------------------------

def test_serialize_chain_link_preserves_transfer_warning():
    """Transfer links may carry a warning string (e.g. SEV note, tight transfer);
    the serializer must round-trip it into the JSON payload."""
    from feedback_client import _serialize_chain_link
    link = ChainLink(
        type="transfer",
        connection=make_connection("A", "10:00", "B", "10:10"),
        warning="Knapper Umstieg! Nur 10 Min.",
    )
    d = _serialize_chain_link(link)
    assert d["type"] == "transfer"
    assert d["warning"] == "Knapper Umstieg! Nur 10 Min."


def test_render_issue_body_shows_sev_badge_when_replacement_service():
    """A connection with `is_replacement_service=True` on any leg must surface
    as `🚌 SEV` in the visible chain line."""
    from feedback_client import build_payload, render_issue_body
    sev_conn = make_connection("A", "08:00", "B", "08:30", is_replacement=True)
    plan = DayPlan(chain=[
        ChainLink(type="outbound", connection=sev_conn),
        ChainLink(type="tour", tour=make_tour(1, "08:35", "B", "09:30", "C", 25.00)),
        ChainLink(type="inbound", connection=make_connection("C", "09:35", "D", "10:00")),
    ])
    payload = build_payload(
        plan=OptimizationResult(winner=plan, alternative=None),
        inputs=make_inputs(), tours=[],
        feedback_type="app-error", feedback_text="x" * 10,
        app_version="0.4.2", submitted_at=FIXED_TS,
    )
    body = render_issue_body(payload)
    assert "🚌 SEV" in body
