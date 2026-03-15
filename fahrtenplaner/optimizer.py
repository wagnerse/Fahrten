"""Tourenoptimierung mit DAG-Longest-Path-DP für maximalen Verdienst."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Callable, Optional

from models import Tour, Connection, ChainLink, DayPlan
from db_client import (
    batch_lookup_stations,
    check_reachability_with_ids,
    stations_match,
)


# Minimale Umstiegszeit in Minuten
MIN_TRANSFER_MINUTES = 5
# Warnung bei knappen Umstiegen
TIGHT_TRANSFER_MINUTES = 15
# Maximale sinnvolle Wartezeit zwischen Touren (Stunden)
MAX_TRANSFER_GAP_HOURS = 12


def optimize_day(
    tours: list[Tour],
    home_station: str,
    dest_station: str,
    earliest_departure: datetime,
    latest_return: datetime,
    progress_callback: Optional[Callable[[float, str], None]] = None,
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
    n = len(tours)

    if n == 0:
        return DayPlan()

    def report(pct: float, msg: str):
        if progress_callback:
            progress_callback(pct, msg)

    # ========== Station Pre-Batch ==========
    report(0.02, f"Stationen auflösen ({n} Touren)...")

    station_names: set[str] = {home_station, dest_station}
    for t in tours:
        station_names.add(t.departure_station)
        station_names.add(t.arrival_station)

    station_ids = batch_lookup_stations(list(station_names))

    def get_id(name: str) -> Optional[str]:
        info = station_ids.get(name)
        return info["id"] if info else None

    home_id = get_id(home_station)
    dest_id = get_id(dest_station)

    if not home_id:
        report(1.0, f"Station '{home_station}' nicht gefunden!")
        return DayPlan()
    if not dest_id:
        report(1.0, f"Station '{dest_station}' nicht gefunden!")
        return DayPlan()

    report(0.05, f"{len(station_names)} Stationen aufgelöst")

    api_calls = 0

    # ========== Phase 1/3: Anreise (Home → Tour-Start) ==========
    can_reach_from_home: list[Optional[Connection]] = [None] * n

    for i, tour in enumerate(tours):
        dep_id = get_id(tour.departure_station)
        if not dep_id:
            report(
                0.05 + 0.15 * (i + 1) / n,
                f"Phase 1/3: Anreise ({i+1}/{n}) – Station nicht gefunden",
            )
            continue

        if stations_match(home_station, tour.departure_station):
            can_reach_from_home[i] = Connection(legs=[])
        else:
            buffer = timedelta(minutes=MIN_TRANSFER_MINUTES)
            must_arrive = tour.departure_dt - buffer

            conn = check_reachability_with_ids(
                home_id, dep_id, earliest_departure, must_arrive,
            )
            can_reach_from_home[i] = conn
            api_calls += 1

        report(
            0.05 + 0.15 * (i + 1) / n,
            f"Phase 1/3: Anreise ({i+1}/{n}) – Tour {tour.tour_nr}",
        )

    # Unerreichbare Touren entfernen
    reachable = [i for i in range(n) if can_reach_from_home[i] is not None]
    removed = n - len(reachable)

    if not reachable:
        report(1.0, "Keine Tour von zuhause erreichbar!")
        return DayPlan()

    if removed > 0:
        report(0.20, f"Phase 1/3 fertig: {removed} Touren nicht erreichbar → entfernt")

    # Auf erreichbare Touren reduzieren
    tours = [tours[i] for i in reachable]
    can_reach_from_home = [can_reach_from_home[i] for i in reachable]
    n = len(tours)

    # Touren nach Abfahrtszeit sortieren → DAG-Eigenschaft sicherstellen
    # (jede gültige Kante zeigt nur vorwärts im sortierten Array)
    sorted_order = sorted(range(n), key=lambda i: tours[i].departure_dt)
    tours = [tours[i] for i in sorted_order]
    can_reach_from_home = [can_reach_from_home[i] for i in sorted_order]

    # ========== Phase 2/3: Tour-zu-Tour Transfers (mit Pruning) ==========
    edge: list[list[Optional[Connection]]] = [[None] * n for _ in range(n)]

    skipped_time = 0
    skipped_same = 0
    transfer_pairs: list[tuple[int, int]] = []

    for i in range(n):
        for j in range(i + 1, n):
            tour_i = tours[i]
            tour_j = tours[j]

            # Zeitlich unmöglich oder zu knapp
            gap_seconds = (tour_j.departure_dt - tour_i.arrival_dt).total_seconds()
            gap_minutes = gap_seconds / 60

            if gap_minutes < MIN_TRANSFER_MINUTES:
                skipped_time += 1
                continue

            # Zu lange Wartezeit → alle weiteren j auch (sortiert nach Abfahrt)
            if gap_minutes > max_transfer_gap_hours * 60:
                skipped_time += 1
                break

            # Gleiche Station → kein API-Call nötig
            if stations_match(tour_i.arrival_station, tour_j.departure_station):
                edge[i][j] = Connection(legs=[])
                skipped_same += 1
                continue

            transfer_pairs.append((i, j))

    api_needed = len(transfer_pairs)
    total_possible = n * (n - 1) // 2
    report(
        0.22,
        f"Phase 2/3: {skipped_time} zeitlich unmöglich, "
        f"{skipped_same} gleiche Station, {api_needed} API-Calls nötig "
        f"(von {total_possible} möglichen)",
    )

    for idx, (i, j) in enumerate(transfer_pairs):
        tour_i = tours[i]
        tour_j = tours[j]

        from_id = get_id(tour_i.arrival_station)
        to_id = get_id(tour_j.departure_station)

        if not from_id or not to_id:
            continue

        buffer = timedelta(minutes=MIN_TRANSFER_MINUTES)
        conn = check_reachability_with_ids(
            from_id, to_id,
            tour_i.arrival_dt + buffer,
            tour_j.departure_dt - buffer,
        )
        edge[i][j] = conn
        api_calls += 1

        report(
            0.22 + 0.38 * (idx + 1) / max(api_needed, 1),
            f"Phase 2/3: Transfer {idx+1}/{api_needed} – "
            f"Tour {tour_i.tour_nr} → {tour_j.tour_nr}",
        )

    # ========== Phase 3/3: Rückreise (Tour-Ende → Dest) ==========
    can_reach_to_dest: list[Optional[Connection]] = [None] * n

    for i, tour in enumerate(tours):
        arr_id = get_id(tour.arrival_station)
        if not arr_id:
            report(
                0.62 + 0.13 * (i + 1) / n,
                f"Phase 3/3: Rückreise ({i+1}/{n}) – Station nicht gefunden",
            )
            continue

        if stations_match(tour.arrival_station, dest_station):
            can_reach_to_dest[i] = Connection(legs=[])
        else:
            buffer = timedelta(minutes=MIN_TRANSFER_MINUTES)
            conn = check_reachability_with_ids(
                arr_id, dest_id,
                tour.arrival_dt + buffer,
                latest_return,
            )
            can_reach_to_dest[i] = conn
            api_calls += 1

        report(
            0.62 + 0.13 * (i + 1) / n,
            f"Phase 3/3: Rückreise ({i+1}/{n}) – Tour {tour.tour_nr}",
        )

    report(
        0.77,
        f"Erreichbarkeitsgraph fertig – {api_calls} API-Calls, "
        f"{skipped_time + skipped_same} übersprungen",
    )

    # ========== DAG-Longest-Path-DP ==========
    report(0.78, "Optimiere Tourenkette (DAG-DP)...")

    NEG_INF = float("-inf")

    # dp[j] = maximaler Verdienst einer Kette, die bei Tour j endet
    dp = [NEG_INF] * n
    # pred[j] = Vorgänger-Tour in der optimalen Kette (-1 = kein Vorgänger)
    pred = [-1] * n

    # Initialisierung: Touren, die direkt von zuhause erreichbar sind
    for i in range(n):
        if can_reach_from_home[i] is not None:
            dp[i] = tours[i].euros

    # DP-Transition: für jede Tour j, prüfe alle möglichen Vorgänger i < j
    for j in range(n):
        for i in range(j):
            if edge[i][j] is not None and dp[i] != NEG_INF:
                new_val = dp[i] + tours[j].euros
                if new_val > dp[j]:
                    dp[j] = new_val
                    pred[j] = i

    report(0.88, "Beste Route wird rekonstruiert...")

    # ========== Beste Kette finden ==========

    best_val = NEG_INF
    best_j = -1

    for j in range(n):
        if dp[j] != NEG_INF and can_reach_to_dest[j] is not None:
            if dp[j] > best_val:
                best_val = dp[j]
                best_j = j

    if best_j == -1:
        report(1.0, "Keine gültige Tourenkette gefunden.")
        return DayPlan()

    # Kette rückwärts rekonstruieren
    chain_indices = []
    cur = best_j
    while cur != -1:
        chain_indices.append(cur)
        cur = pred[cur]
    chain_indices.reverse()

    report(0.93, "Tagesplan wird zusammengestellt...")

    # ========== DayPlan bauen ==========

    plan = DayPlan()

    # Anreise
    first_idx = chain_indices[0]
    anreise_conn = can_reach_from_home[first_idx]
    if anreise_conn and anreise_conn.legs:
        warning = None
        if anreise_conn.has_replacement_service:
            warning = "Schienenersatzverkehr auf der Anreise!"
        plan.chain.append(ChainLink(
            type="anreise",
            connection=anreise_conn,
            warning=warning,
        ))

    # Touren + Transfers
    for pos, idx in enumerate(chain_indices):
        tour = tours[idx]

        # Tour
        plan.chain.append(ChainLink(type="tour", tour=tour))

        # Transfer zur nächsten Tour
        if pos < len(chain_indices) - 1:
            next_idx = chain_indices[pos + 1]
            transfer_conn = edge[idx][next_idx]
            warning = _check_transfer_warning(tour, tours[next_idx], transfer_conn)
            plan.chain.append(ChainLink(
                type="transfer",
                connection=transfer_conn,
                warning=warning,
            ))

    # Rückreise
    last_idx = chain_indices[-1]
    rückreise_conn = can_reach_to_dest[last_idx]
    if rückreise_conn and rückreise_conn.legs:
        warning = None
        if rückreise_conn.has_replacement_service:
            warning = "Schienenersatzverkehr auf der Rückreise!"
        plan.chain.append(ChainLink(
            type="rückreise",
            connection=rückreise_conn,
            warning=warning,
        ))

    report(
        1.0,
        f"Fertig! {plan.num_tours} Touren, {plan.total_euros:.2f}€ "
        f"({api_calls} API-Calls)",
    )
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

    # Zeitpuffer zwischen Tour-Ende und nächstem Tour-Start
    gap = to_tour.departure_dt - from_tour.arrival_dt
    gap_minutes = gap.total_seconds() / 60

    if gap_minutes < TIGHT_TRANSFER_MINUTES:
        warnings.append(f"Knapper Umstieg! Nur {int(gap_minutes)} Min.")

    return " | ".join(warnings) if warnings else None
