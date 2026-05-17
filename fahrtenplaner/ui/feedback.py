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

from feedback_client import (
    FeedbackAuthError, FeedbackBodyTooLarge, FeedbackError, FeedbackNetworkError,
    build_payload, render_issue_body, render_issue_title, submit_to_github,
)


_TYPE_OPTIONS = {
    "better-route": "Es gab eine bessere Route",
    "app-error":    "App-Fehler",
}


def _fmt_eur(amount: float) -> str:
    """Local copy of ui/render.py::_fmt_eur — small enough to duplicate
    rather than couple modules."""
    return f"{amount:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")


def _secrets_present() -> bool:
    """True iff [github] block in secrets.toml has both `token` and `repo`."""
    try:
        gh = st.secrets.get("github", {})
    except Exception:
        return False
    return bool(gh.get("token")) and bool(gh.get("repo"))


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
            f"max. Auto-Anfahrt {inputs['max_car_minutes']} Min"
            + (
                f" · Kilometerpauschale {inputs['fuel_refund_per_km'] * 100:.0f} ct/km"
                if inputs.get("fuel_refund_per_km")
                else ""
            )
            + "\n"
            f"- **Anzahl verfügbarer Touren am Tag:** {len(tours)}"
        )
        st.markdown("**Gewinner-Kette:**")
        for link in plan.winner.chain:
            st.markdown(f"- {link.label}")
        if plan.has_alternative:
            st.markdown("**Alternative:**")
            for link in plan.alternative.chain:
                st.markdown(f"- {link.label}")


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
        oversized = st.session_state.get("_feedback_oversized_body")
        failed = st.session_state.get("_feedback_failed_body")
        if oversized:
            _clipboard_block(oversized)
        elif failed:
            _clipboard_block(failed)

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
            _handle_submit(plan, tours, inputs)
            st.rerun()
