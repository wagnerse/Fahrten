"""Tourenoptimierung mit DAG-Longest-Path-DP für maximalen Verdienst.

Die Datei ist in kleine, einzeln lesbare Funktionen zerlegt; ``optimize_day``
ist nur noch ein Orchestrator, der die Phasen aufruft.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Callable, Optional

from models import CarLeg, ChainLink, Connection, DayPlan, Tour
from transit_client import (
    batch_lookup_stations,
    check_reachability_with_ids,
    driving_info,
    stations_match,
)


# Minimale Umstiegszeit in Minuten
MIN_TRANSFER_MINUTES = 5
# Warnung bei knappen Umstiegen
TIGHT_TRANSFER_MINUTES = 15
# Maximale sinnvolle Wartezeit zwischen Touren (Stunden)
MAX_TRANSFER_GAP_HOURS = 12

NEG_INF = float("-inf")

# Type alias for the progress callback we pass into helpers.
ProgressCb = Optional[Callable[[float, str], None]]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _no_progress(_pct: float, _msg: str) -> None:
    """Fallback progress sink so helpers can call it unconditionally."""


def _normalize_progress(callback: ProgressCb) -> Callable[[float, str], None]:
    return callback if callback is not None else _no_progress


def _id_lookup(station_ids: dict) -> Callable[[str], Optional[str]]:
    """Return a function that maps a station name to its place_id (or None)."""
    def get_id(name: str) -> Optional[str]:
        info = station_ids.get(name)
        return info["id"] if info else None
    return get_id


def _collect_station_names(tours: list[Tour], home: str, dest: str) -> set[str]:
    names: set[str] = {home, dest}
    for t in tours:
        names.add(t.departure_station)
        names.add(t.arrival_station)
    return names


# ---------------------------------------------------------------------------
# Phase 1 — Anreise
# ---------------------------------------------------------------------------

def _outbound_for_tour(
    tour: Tour,
    home_station: str,
    home_id: str,
    get_id: Callable[[str], Optional[str]],
    earliest_departure: datetime,
) -> Optional[Connection]:
    """Resolve the outbound from home to one tour's departure station, or None."""
    dep_id = get_id(tour.departure_station)
    if not dep_id:
        return None
    if stations_match(home_station, tour.departure_station):
        return Connection(legs=[])
    must_arrive = tour.departure_dt - timedelta(minutes=MIN_TRANSFER_MINUTES)
    return check_reachability_with_ids(home_id, dep_id, earliest_departure, must_arrive)


def _compute_outbound(
    tours: list[Tour],
    home_station: str,
    home_id: str,
    get_id: Callable[[str], Optional[str]],
    earliest_departure: datetime,
    report: Callable[[float, str], None],
) -> tuple[list[Optional[Connection]], int]:
    """Phase 1: per-tour reachability from home. Returns (per-tour outbound, api_calls)."""
    n = len(tours)
    outbound: list[Optional[Connection]] = [None] * n
    api_calls = 0
    for i, tour in enumerate(tours):
        conn = _outbound_for_tour(
            tour, home_station, home_id, get_id, earliest_departure,
        )
        outbound[i] = conn
        # We made an API call only if we actually did the reachability lookup.
        if conn is not None and conn.legs:
            api_calls += 1
        report(
            0.05 + 0.15 * (i + 1) / n,
            f"Phase 1/3: Anreise ({i + 1}/{n}) – Tour {tour.tour_nr}",
        )
    return outbound, api_calls


def _filter_and_sort_reachable(
    tours: list[Tour], outbound: list[Optional[Connection]],
) -> tuple[list[Tour], list[Optional[Connection]]]:
    """Drop unreachable tours and sort the remaining by departure_dt (DAG order)."""
    reachable = [(tours[i], outbound[i]) for i in range(len(tours)) if outbound[i] is not None]
    reachable.sort(key=lambda pair: pair[0].departure_dt)
    if not reachable:
        return [], []
    sorted_tours, sorted_outbound = zip(*reachable, strict=True)
    return list(sorted_tours), list(sorted_outbound)


# ---------------------------------------------------------------------------
# Phase 2 — Transfer matrix
# ---------------------------------------------------------------------------

def _classify_transfer(
    tour_i: Tour, tour_j: Tour, max_transfer_gap_hours: float,
) -> str:
    """Categorize a tour-pair as 'time' (skip), 'break' (stop iter), 'same' (no API), or 'api'."""
    gap_minutes = (tour_j.departure_dt - tour_i.arrival_dt).total_seconds() / 60
    if gap_minutes < MIN_TRANSFER_MINUTES:
        return "time"
    if gap_minutes > max_transfer_gap_hours * 60:
        return "break"
    if stations_match(tour_i.arrival_station, tour_j.departure_station):
        return "same"
    return "api"


def _classify_transfer_pairs(
    tours: list[Tour], max_transfer_gap_hours: float,
) -> tuple[list[list[Optional[Connection]]], list[tuple[int, int]], int, int]:
    """Build the empty edge matrix, fill same-station edges, return the API-needed pairs."""
    n = len(tours)
    edge: list[list[Optional[Connection]]] = [[None] * n for _ in range(n)]
    transfer_pairs: list[tuple[int, int]] = []
    skipped_time = 0
    skipped_same = 0

    for i in range(n):
        for j in range(i + 1, n):
            kind = _classify_transfer(tours[i], tours[j], max_transfer_gap_hours)
            if kind == "time":
                skipped_time += 1
                continue
            if kind == "break":
                skipped_time += 1
                break
            if kind == "same":
                edge[i][j] = Connection(legs=[])
                skipped_same += 1
                continue
            transfer_pairs.append((i, j))
    return edge, transfer_pairs, skipped_time, skipped_same


def _resolve_transfer_pairs(
    transfer_pairs: list[tuple[int, int]],
    tours: list[Tour],
    edge: list[list[Optional[Connection]]],
    get_id: Callable[[str], Optional[str]],
    report: Callable[[float, str], None],
) -> int:
    """Make the API calls for the transfer pairs that need them."""
    api_calls = 0
    api_needed = max(len(transfer_pairs), 1)
    buffer = timedelta(minutes=MIN_TRANSFER_MINUTES)
    for idx, (i, j) in enumerate(transfer_pairs):
        tour_i, tour_j = tours[i], tours[j]
        from_id = get_id(tour_i.arrival_station)
        to_id = get_id(tour_j.departure_station)
        if not from_id or not to_id:
            continue
        edge[i][j] = check_reachability_with_ids(
            from_id, to_id,
            tour_i.arrival_dt + buffer,
            tour_j.departure_dt - buffer,
        )
        api_calls += 1
        report(
            0.22 + 0.38 * (idx + 1) / api_needed,
            f"Phase 2/3: Transfer {idx + 1}/{len(transfer_pairs)} – "
            f"Tour {tour_i.tour_nr} → {tour_j.tour_nr}",
        )
    return api_calls


def _compute_transfer_matrix(
    tours: list[Tour],
    get_id: Callable[[str], Optional[str]],
    max_transfer_gap_hours: float,
    report: Callable[[float, str], None],
) -> tuple[list[list[Optional[Connection]]], int]:
    """Phase 2: tour-to-tour transfer feasibility matrix."""
    edge, transfer_pairs, skipped_time, skipped_same = _classify_transfer_pairs(
        tours, max_transfer_gap_hours,
    )
    total_possible = len(tours) * (len(tours) - 1) // 2
    report(
        0.22,
        f"Phase 2/3: {skipped_time} zeitlich unmöglich, "
        f"{skipped_same} gleiche Station, {len(transfer_pairs)} API-Calls nötig "
        f"(von {total_possible} möglichen)",
    )
    api_calls = _resolve_transfer_pairs(transfer_pairs, tours, edge, get_id, report)
    return edge, api_calls


# ---------------------------------------------------------------------------
# Phase 3 — Rückreise
# ---------------------------------------------------------------------------

def _inbound_for_tour(
    tour: Tour,
    dest_station: str,
    dest_id: str,
    get_id: Callable[[str], Optional[str]],
    latest_return: datetime,
) -> Optional[Connection]:
    arr_id = get_id(tour.arrival_station)
    if not arr_id:
        return None
    if stations_match(tour.arrival_station, dest_station):
        return Connection(legs=[])
    return check_reachability_with_ids(
        arr_id, dest_id,
        tour.arrival_dt + timedelta(minutes=MIN_TRANSFER_MINUTES),
        latest_return,
    )


def _compute_inbound(
    tours: list[Tour],
    dest_station: str,
    dest_id: str,
    get_id: Callable[[str], Optional[str]],
    latest_return: datetime,
    report: Callable[[float, str], None],
) -> tuple[list[Optional[Connection]], int]:
    """Phase 3: per-tour reachability from arrival station to home/destination."""
    n = len(tours)
    inbound: list[Optional[Connection]] = [None] * n
    api_calls = 0
    for i, tour in enumerate(tours):
        conn = _inbound_for_tour(tour, dest_station, dest_id, get_id, latest_return)
        inbound[i] = conn
        if conn is not None and conn.legs:
            api_calls += 1
        report(
            0.62 + 0.13 * (i + 1) / n,
            f"Phase 3/3: Rückreise ({i + 1}/{n}) – Tour {tour.tour_nr}",
        )
    return inbound, api_calls


# ---------------------------------------------------------------------------
# DAG-DP and chain reconstruction
# ---------------------------------------------------------------------------

def _run_dag_dp(
    tours: list[Tour],
    outbound: list[Optional[Connection]],
    edge: list[list[Optional[Connection]]],
) -> tuple[list[float], list[int]]:
    """DAG longest-path DP. dp[j] = max revenue ending at tour j; pred[j] = predecessor."""
    n = len(tours)
    dp: list[float] = [NEG_INF] * n
    pred: list[int] = [-1] * n

    for i in range(n):
        if outbound[i] is not None:
            dp[i] = tours[i].euros

    for j in range(n):
        for i in range(j):
            if edge[i][j] is None or dp[i] == NEG_INF:
                continue
            new_val = dp[i] + tours[j].euros
            if new_val > dp[j]:
                dp[j] = new_val
                pred[j] = i
    return dp, pred


def _find_best_chain_end(
    dp: list[float], inbound: list[Optional[Connection]],
) -> int:
    """Pick the j with highest dp[j] that also has a valid Rückreise. -1 if none."""
    best_val = NEG_INF
    best_j = -1
    for j, val in enumerate(dp):
        if val == NEG_INF or inbound[j] is None:
            continue
        if val > best_val:
            best_val = val
            best_j = j
    return best_j


def _reconstruct_chain(pred: list[int], best_j: int) -> list[int]:
    """Backtrack via predecessor pointers to recover the chain (in forward order)."""
    indices: list[int] = []
    cur = best_j
    while cur != -1:
        indices.append(cur)
        cur = pred[cur]
    indices.reverse()
    return indices


# ---------------------------------------------------------------------------
# DayPlan synthesis
# ---------------------------------------------------------------------------

def _outbound_link(connection: Optional[Connection]) -> Optional[ChainLink]:
    if not connection or not connection.legs:
        return None
    warning = "Schienenersatzverkehr auf der Anreise!" if connection.has_replacement_service else None
    return ChainLink(type="outbound", connection=connection, warning=warning)


def _inbound_link(connection: Optional[Connection]) -> Optional[ChainLink]:
    if not connection or not connection.legs:
        return None
    warning = "Schienenersatzverkehr auf der Rückreise!" if connection.has_replacement_service else None
    return ChainLink(type="inbound", connection=connection, warning=warning)


def _transfer_link(
    from_tour: Tour, to_tour: Tour, connection: Optional[Connection],
) -> ChainLink:
    return ChainLink(
        type="transfer",
        connection=connection,
        warning=_check_transfer_warning(from_tour, to_tour, connection),
    )


def _build_dayplan(
    tours: list[Tour],
    chain_indices: list[int],
    outbound: list[Optional[Connection]],
    edge: list[list[Optional[Connection]]],
    inbound: list[Optional[Connection]],
) -> DayPlan:
    plan = DayPlan()
    if not chain_indices:
        return plan

    outbound_link = _outbound_link(outbound[chain_indices[0]])
    if outbound_link is not None:
        plan.chain.append(outbound_link)

    for pos, idx in enumerate(chain_indices):
        plan.chain.append(ChainLink(type="tour", tour=tours[idx]))
        if pos < len(chain_indices) - 1:
            next_idx = chain_indices[pos + 1]
            plan.chain.append(_transfer_link(
                tours[idx], tours[next_idx], edge[idx][next_idx],
            ))

    inbound_link = _inbound_link(inbound[chain_indices[-1]])
    if inbound_link is not None:
        plan.chain.append(inbound_link)

    return plan


# ---------------------------------------------------------------------------
# Top-level orchestration
# ---------------------------------------------------------------------------

def optimize_day(
    tours: list[Tour],
    home_station: str,
    dest_station: str,
    earliest_departure: datetime,
    latest_return: datetime,
    progress_callback: ProgressCb = None,
    max_transfer_gap_hours: float = MAX_TRANSFER_GAP_HOURS,
) -> DayPlan:
    """
    Berechnet die optimale Tourenkette für einen Tag.

    Args:
        tours: Verfügbare Touren für diesen Tag
        home_station: Abfahrtsbahnhof (z.B. "Prenzlau")
        dest_station: Ankunftsbahnhof (z.B. "Stralsund" oder gleich wie home)
        earliest_departure: Früheste Abfahrt von zuhause
        latest_return: Späteste Rückkehr am Zielbahnhof
        progress_callback: Optional (progress: 0-1, message: str)
        max_transfer_gap_hours: Maximale Wartezeit zwischen Touren in Stunden
    """
    if not tours:
        return DayPlan()

    report = _normalize_progress(progress_callback)

    # ----- Station resolution ------------------------------------------------
    n = len(tours)
    report(0.02, f"Stationen auflösen ({n} Touren)...")
    station_names = _collect_station_names(tours, home_station, dest_station)
    station_ids = batch_lookup_stations(list(station_names))
    get_id = _id_lookup(station_ids)
    home_id = get_id(home_station)
    dest_id = get_id(dest_station)
    if not home_id:
        report(1.0, f"Station '{home_station}' nicht gefunden!")
        return DayPlan()
    if not dest_id:
        report(1.0, f"Station '{dest_station}' nicht gefunden!")
        return DayPlan()
    report(0.05, f"{len(station_names)} Stationen aufgelöst")

    # ----- Phase 1: Anreise --------------------------------------------------
    outbound, api1 = _compute_outbound(
        tours, home_station, home_id, get_id, earliest_departure, report,
    )
    tours, outbound = _filter_and_sort_reachable(tours, outbound)
    if not tours:
        report(1.0, "Keine Tour von zuhause erreichbar!")
        return DayPlan()
    report(0.20, f"Phase 1/3 fertig: {len(tours)} Touren erreichbar")

    # ----- Phase 2: Transfer matrix -----------------------------------------
    edge, api2 = _compute_transfer_matrix(tours, get_id, max_transfer_gap_hours, report)

    # ----- Phase 3: Rückreise ------------------------------------------------
    inbound, api3 = _compute_inbound(
        tours, dest_station, dest_id, get_id, latest_return, report,
    )
    api_calls = api1 + api2 + api3
    report(0.77, f"Erreichbarkeitsgraph fertig – {api_calls} API-Calls")

    # ----- DP and reconstruction --------------------------------------------
    report(0.78, "Optimiere Tourenkette (DAG-DP)...")
    dp, pred = _run_dag_dp(tours, outbound, edge)
    report(0.88, "Beste Route wird rekonstruiert...")
    best_j = _find_best_chain_end(dp, inbound)
    if best_j == -1:
        report(1.0, "Keine gültige Tourenkette gefunden.")
        return DayPlan()
    chain_indices = _reconstruct_chain(pred, best_j)

    # ----- DayPlan synthesis ------------------------------------------------
    report(0.93, "Tagesplan wird zusammengestellt...")
    plan = _build_dayplan(tours, chain_indices, outbound, edge, inbound)
    report(
        1.0,
        f"Fertig! {plan.num_tours} Touren, {plan.total_euros:.2f}€ "
        f"({api_calls} API-Calls)",
    )
    return plan


def optimize_day_car_mode(
    tours: list[Tour],
    home_station: str,
    earliest_departure: datetime,
    latest_return: datetime,
    max_car_minutes: int,
    fuel_consumption: float,
    fuel_price: float,
    progress_callback: ProgressCb = None,
    max_transfer_gap_hours: float = MAX_TRANSFER_GAP_HOURS,
) -> DayPlan:
    """Find the best chain that starts AND ends at a tour-departure station
    within `max_car_minutes` driving radius from `home_station`. The user drives
    to that station, does the tour chain, then drives back from there.
    """
    if not tours or max_car_minutes <= 0:
        return DayPlan()

    report = _normalize_progress(progress_callback)

    # Resolve all stations once (shared with potential transit pass via cache).
    station_names = _collect_station_names(tours, home_station, home_station)
    station_ids = batch_lookup_stations(list(station_names))
    get_id = _id_lookup(station_ids)
    home_id = get_id(home_station)
    if not home_id:
        return DayPlan()

    # Build the transit transfer matrix once (shared with sister transit-mode call).
    sorted_tours = sorted(tours, key=lambda t: t.departure_dt)
    edge, _ = _compute_transfer_matrix(
        sorted_tours, get_id, max_transfer_gap_hours, report,
    )

    candidates = sorted({t.departure_station for t in sorted_tours})

    cost_per_km = (fuel_consumption / 100.0) * fuel_price
    best_plan = DayPlan()

    report(0.0, f"Auto-Modus: prüfe {len(candidates)} Kandidaten...")

    for candidate in candidates:
        cand_id = get_id(candidate)
        if not cand_id:
            continue
        info = driving_info(home_id, cand_id)
        if info is None:
            continue
        drive_min, drive_km = info
        if drive_min > max_car_minutes:
            continue

        report(
            (candidates.index(candidate) + 1) / max(len(candidates), 1),
            f"Auto-Modus: {candidate} ({drive_min} min, {drive_km:.0f} km)",
        )

        plan = _build_car_chain_for_candidate(
            sorted_tours, edge, candidate, drive_min, drive_km, cost_per_km,
            earliest_departure, latest_return, home_station,
        )
        if plan.net_euros > best_plan.net_euros:
            best_plan = plan

    if best_plan.num_tours > 0:
        report(1.0, f"Auto-Modus fertig: {best_plan.total_euros:.2f} € brutto")
    else:
        report(1.0, "Auto-Modus: keine Kette gefunden")

    return best_plan


def _build_car_chain_for_candidate(
    tours: list[Tour],
    edge: list[list[Optional[Connection]]],
    candidate: str,
    drive_min: int,
    drive_km: float,
    cost_per_km: float,
    earliest_departure: datetime,
    latest_return: datetime,
    home_station: str,
) -> DayPlan:
    """Run the constrained DAG-DP for one candidate car-park station and
    materialize the resulting chain into a DayPlan with car legs.
    """
    n = len(tours)
    car_arrival = earliest_departure + timedelta(minutes=drive_min)
    latest_tour_arrival = latest_return - timedelta(minutes=drive_min)

    dp: list[float] = [NEG_INF] * n
    pred: list[int] = [-1] * n

    # Seed: tours that start at the candidate station after car arrival.
    for i, tour in enumerate(tours):
        if stations_match(tour.departure_station, candidate) and tour.departure_dt >= car_arrival:
            dp[i] = tour.euros

    # Standard DAG-DP transition (uses shared transfer matrix).
    for j in range(n):
        for i in range(j):
            if edge[i][j] is None or dp[i] == NEG_INF:
                continue
            new_val = dp[i] + tours[j].euros
            if new_val > dp[j]:
                dp[j] = new_val
                pred[j] = i

    # Best end: tour ending at the candidate station, with time to drive back.
    best_val = NEG_INF
    best_j = -1
    for j, val in enumerate(dp):
        if val == NEG_INF:
            continue
        if not stations_match(tours[j].arrival_station, candidate):
            continue
        if tours[j].arrival_dt > latest_tour_arrival:
            continue
        if val > best_val:
            best_val = val
            best_j = j

    if best_j == -1:
        return DayPlan()

    chain_indices = _reconstruct_chain(pred, best_j)
    leg_cost = drive_km * cost_per_km

    plan = DayPlan()
    plan.chain.append(ChainLink(
        type="car_outbound",
        car_leg=CarLeg(
            from_station=home_station, to_station=candidate,
            minutes=drive_min, km=drive_km, cost=leg_cost,
        ),
    ))
    for pos, idx in enumerate(chain_indices):
        plan.chain.append(ChainLink(type="tour", tour=tours[idx]))
        if pos < len(chain_indices) - 1:
            next_idx = chain_indices[pos + 1]
            plan.chain.append(_transfer_link(
                tours[idx], tours[next_idx], edge[idx][next_idx],
            ))
    plan.chain.append(ChainLink(
        type="car_inbound",
        car_leg=CarLeg(
            from_station=candidate, to_station=home_station,
            minutes=drive_min, km=drive_km, cost=leg_cost,
        ),
    ))
    return plan


def _check_transfer_warning(
    from_tour: Tour,
    to_tour: Tour,
    connection: Optional[Connection],
) -> Optional[str]:
    """Prüft auf knappe Umstiege und SEV."""
    warnings = []
    if connection and connection.has_replacement_service:
        warnings.append("Schienenersatzverkehr!")
    gap_minutes = (to_tour.departure_dt - from_tour.arrival_dt).total_seconds() / 60
    if gap_minutes < TIGHT_TRANSFER_MINUTES:
        warnings.append(f"Knapper Umstieg! Nur {int(gap_minutes)} Min.")
    return " | ".join(warnings) if warnings else None
