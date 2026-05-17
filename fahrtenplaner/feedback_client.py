"""GitHub-Issues-based feedback submission. No Streamlit imports — fully testable."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Literal, Optional

import requests

from models import (
    ChainLink, DayPlan, OptimizationResult, Tour,
)


# 4 KB safety margin under GitHub's 65 KB issue-body cap.
_BODY_SIZE_LIMIT = 60_000

_FeedbackType = Literal["better-route", "app-error"]

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
    feedback_type: _FeedbackType,
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


# ---------------------------------------------------------------------------
# Markdown rendering — title
# ---------------------------------------------------------------------------

def render_issue_title(payload: dict) -> str:
    """Render the GitHub issue title from a payload (Section 2 of the design spec)."""
    inputs = payload["inputs"]
    type_label = _TYPE_LABEL_DE[payload["feedback"]["type"]]
    return (
        f"Feedback {inputs['date']}: "
        f"{inputs['home_station']} → {inputs['dest_station']} "
        f"({type_label})"
    )


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
    refund = inp.get("fuel_refund_per_km")
    refund_part = (
        f" · Kilometerpauschale {refund * 100:.0f} ct/km" if refund else ""
    )
    params = (
        f"Abfahrt ab {inp['earliest_departure']} · "
        f"Rückkehr bis {inp['latest_return']} · "
        f"max. Pause {inp['max_transfer_gap_minutes']} Min · "
        f"max. Auto-Anfahrt {inp['max_car_minutes']} Min"
        f"{refund_part}"
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
