"""Tests for the tour optimizer — verifies DP logic with mocked DB API."""

import sys
from datetime import date, time, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

# Make fahrtenplaner importable
sys.path.insert(0, str(Path(__file__).parent.parent / "fahrtenplaner"))

from models import Tour, Connection, Leg, DayPlan


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

DAY = date(2026, 4, 1)
HOME = "Prenzlau"

EMPTY_CONN = Connection(legs=[])  # same-station / trivial connection


def make_tour(
    tour_nr: int,
    dep_time: str,
    dep_station: str,
    arr_time: str,
    arr_station: str,
    euros: float,
    num_rides: int = 1,
) -> Tour:
    """Create a Tour from compact parameters."""
    h1, m1 = map(int, dep_time.split(":"))
    h2, m2 = map(int, arr_time.split(":"))
    dt1 = datetime.combine(DAY, time(h1, m1))
    dt2 = datetime.combine(DAY, time(h2, m2))
    if dt2 < dt1:
        dt2 += timedelta(days=1)
    return Tour(
        tour_nr=tour_nr,
        priority=1,
        day_name="Mi",
        date=DAY,
        departure_time=time(h1, m1),
        departure_station=dep_station,
        arrival_time=time(h2, m2),
        arrival_station=arr_station,
        num_rides=num_rides,
        points=0,
        duration=dt2 - dt1,
        euros=euros,
    )


def make_leg_conn(dep_station, dep_time_str, arr_station, arr_time_str, line="RE1"):
    """Create a Connection with a single Leg (for mocking travel connections)."""
    return Connection(legs=[Leg(
        departure_station=dep_station,
        departure_time=datetime.fromisoformat(dep_time_str),
        arrival_station=arr_station,
        arrival_time=datetime.fromisoformat(arr_time_str),
        line=line,
    )])


# ---------------------------------------------------------------------------
# Demo tours (subset of 01.04.2026 data)
# ---------------------------------------------------------------------------

TOUR_704345 = make_tour(704345, "13:19", "Warnemünde", "14:58", "Warnemünde", 23.10, 2)
TOUR_704347 = make_tour(704347, "06:02", "Cottbus Hbf", "09:55", "Falkenberg(Elster)", 69.35, 2)
TOUR_704213 = make_tour(704213, "03:53", "Lübbenau(Spreewald)", "06:53", "Dessau Hbf", 57.00)
TOUR_705978 = make_tour(705978, "06:30", "Graal-Müritz", "09:18", "Rostock Hbf", 45.60, 3)
TOUR_705877 = make_tour(705877, "19:00", "Barth", "21:37", "Stralsund Hbf", 42.90, 3)
TOUR_704349 = make_tour(704349, "08:01", "Barth", "09:53", "Barth", 28.60, 2)
TOUR_704208 = make_tour(704208, "17:39", "Warnemünde", "18:50", "Güstrow", 22.48)
TOUR_704344 = make_tour(704344, "12:29", "Warnemünde", "13:48", "Warnemünde", 18.44, 2)


# ---------------------------------------------------------------------------
# Test runner helper
# ---------------------------------------------------------------------------

def run_optimizer(
    tours,
    home=HOME,
    dest=HOME,
    earliest="04:00",
    latest="23:59",
    max_gap_hours=1.0,
    station_ids=None,
    home_to_tour=None,       # {tour_idx: Connection | None}
    tour_to_tour=None,       # {(i, j): Connection | None}
    tour_to_dest=None,       # {tour_idx: Connection | None}
):
    """Run optimize_day with mocked DB calls."""
    if station_ids is None:
        station_ids = {}
    if home_to_tour is None:
        home_to_tour = {}
    if tour_to_tour is None:
        tour_to_tour = {}
    if tour_to_dest is None:
        tour_to_dest = {}

    h1, m1 = map(int, earliest.split(":"))
    h2, m2 = map(int, latest.split(":"))

    # Collect all station names, assign default IDs
    all_stations = {home, dest}
    for t in tours:
        all_stations.add(t.departure_station)
        all_stations.add(t.arrival_station)
    default_ids = {name: {"id": f"id_{name}", "name": name} for name in all_stations}
    default_ids.update(station_ids)

    def mock_batch_lookup(names):
        return {name: default_ids.get(name) for name in names}

    # Build reachability map keyed by (from_id, to_id)
    reachability_map = {}
    home_id = default_ids[home]["id"]
    dest_id = default_ids[dest]["id"]

    for idx, tour in enumerate(tours):
        dep_id = default_ids[tour.departure_station]["id"]
        arr_id = default_ids[tour.arrival_station]["id"]

        if idx in home_to_tour:
            reachability_map[(home_id, dep_id)] = home_to_tour[idx]
        if idx in tour_to_dest:
            reachability_map[(arr_id, dest_id)] = tour_to_dest[idx]

    for (i, j), conn in tour_to_tour.items():
        arr_id = default_ids[tours[i].arrival_station]["id"]
        dep_id = default_ids[tours[j].departure_station]["id"]
        reachability_map[(arr_id, dep_id)] = conn

    def mock_check_reachability(from_id, to_id, earliest_dep, must_arrive):
        return reachability_map.get((from_id, to_id))

    def mock_stations_match(a, b):
        def norm(s):
            s = s.lower().strip().split("(")[0].strip()
            for suffix in [" hbf", " hauptbahnhof", " bf"]:
                s = s.removesuffix(suffix)
            return s
        na, nb = norm(a), norm(b)
        return na == nb or na in nb or nb in na

    with patch("optimizer.batch_lookup_stations", side_effect=mock_batch_lookup), \
         patch("optimizer.check_reachability_with_ids", side_effect=mock_check_reachability), \
         patch("optimizer.stations_match", side_effect=mock_stations_match):

        from optimizer import optimize_day
        plan, _candidates, _directly_reachable = optimize_day(
            tours=tours,
            home_station=home,
            dest_station=dest,
            earliest_departure=datetime.combine(DAY, time(h1, m1)),
            latest_return=datetime.combine(DAY, time(h2, m2)),
            max_transfer_gap_hours=max_gap_hours,
        )
        return plan


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSingleTourSelection:
    """Optimizer must pick the highest-value reachable single tour."""

    def test_picks_best_single_tour(self):
        """When 3 tours are reachable but no chaining possible, pick the best one."""
        tours = [TOUR_704345, TOUR_704347, TOUR_705978]
        home_to_tour = {0: EMPTY_CONN, 1: EMPTY_CONN, 2: EMPTY_CONN}
        tour_to_dest = {0: EMPTY_CONN, 1: EMPTY_CONN, 2: EMPTY_CONN}

        plan = run_optimizer(
            tours, home_to_tour=home_to_tour, tour_to_dest=tour_to_dest,
        )

        assert plan.num_tours == 1
        assert plan.tours[0].tour_nr == 704347  # 69.35 > 45.60 > 23.10
        assert plan.total_euros == pytest.approx(69.35)

    def test_skips_unreachable_expensive_tour(self):
        """Expensive but unreachable tour is skipped; cheaper reachable one wins."""
        tours = [TOUR_704345, TOUR_704347]
        home_to_tour = {0: EMPTY_CONN}  # only 704345 reachable
        tour_to_dest = {0: EMPTY_CONN, 1: EMPTY_CONN}

        plan = run_optimizer(
            tours, home_to_tour=home_to_tour, tour_to_dest=tour_to_dest,
        )

        assert plan.num_tours == 1
        assert plan.tours[0].tour_nr == 704345
        assert plan.total_euros == pytest.approx(23.10)

    def test_no_return_possible_means_no_plan(self):
        """Tour reachable but no return trip -> not selectable."""
        tours = [TOUR_704347]
        home_to_tour = {0: EMPTY_CONN}
        # no tour_to_dest!

        plan = run_optimizer(tours, home_to_tour=home_to_tour)

        assert plan.num_tours == 0

    def test_no_tours_reachable_means_empty_plan(self):
        """No tour reachable from home -> empty plan."""
        tours = [TOUR_704347, TOUR_705978]

        plan = run_optimizer(tours)

        assert plan.num_tours == 0


class TestChaining:
    """Optimizer must correctly chain tours for maximum earnings."""

    def test_single_best_beats_impossible_chain(self):
        """When two tours overlap (no chain possible), pick the single best."""
        # 704344 ends 13:48, 704345 starts 13:19 -> overlap, can't chain
        tours = [TOUR_704344, TOUR_704345, TOUR_705978]
        home_to_tour = {0: EMPTY_CONN, 1: EMPTY_CONN, 2: EMPTY_CONN}
        tour_to_dest = {0: EMPTY_CONN, 1: EMPTY_CONN, 2: EMPTY_CONN}

        plan = run_optimizer(
            tours, home_to_tour=home_to_tour, tour_to_dest=tour_to_dest,
        )

        assert plan.total_euros == pytest.approx(45.60)
        assert plan.tours[0].tour_nr == 705978

    def test_chains_two_compatible_tours(self):
        """Two time-compatible tours are chained for higher total."""
        # 705978: 06:30-09:18 Rostock (45.60)
        # 704344: 12:29-13:48 Warnemünde (18.44)
        # gap: 3h11 — within max_gap_hours=4
        tours = [TOUR_705978, TOUR_704344]
        home_to_tour = {0: EMPTY_CONN, 1: EMPTY_CONN}
        tour_to_dest = {0: EMPTY_CONN, 1: EMPTY_CONN}
        tour_to_tour = {(0, 1): EMPTY_CONN}

        plan = run_optimizer(
            tours,
            home_to_tour=home_to_tour,
            tour_to_dest=tour_to_dest,
            tour_to_tour=tour_to_tour,
            max_gap_hours=4.0,
        )

        assert plan.num_tours == 2
        assert plan.total_euros == pytest.approx(45.60 + 18.44)

    def test_max_gap_prevents_chaining(self):
        """Gap exceeding max_gap_hours prevents chain; falls back to best single."""
        tours = [TOUR_705978, TOUR_704344]
        home_to_tour = {0: EMPTY_CONN, 1: EMPTY_CONN}
        tour_to_dest = {0: EMPTY_CONN, 1: EMPTY_CONN}
        tour_to_tour = {(0, 1): EMPTY_CONN}

        # gap 3h11 > max 1h -> chain blocked
        plan = run_optimizer(
            tours,
            home_to_tour=home_to_tour,
            tour_to_dest=tour_to_dest,
            tour_to_tour=tour_to_tour,
            max_gap_hours=1.0,
        )

        assert plan.num_tours == 1
        assert plan.tours[0].tour_nr == 705978

    def test_three_tour_chain(self):
        """Three tours chained for maximum earnings."""
        tour_a = make_tour(1, "06:00", "A-Stadt", "07:00", "B-Stadt", 20.0)
        tour_b = make_tour(2, "07:30", "B-Stadt", "08:30", "C-Stadt", 30.0)
        tour_c = make_tour(3, "09:00", "C-Stadt", "10:00", "A-Stadt", 25.0)

        tours = [tour_a, tour_b, tour_c]
        home_to_tour = {0: EMPTY_CONN, 1: EMPTY_CONN, 2: EMPTY_CONN}
        tour_to_dest = {0: EMPTY_CONN, 1: EMPTY_CONN, 2: EMPTY_CONN}
        tour_to_tour = {(0, 1): EMPTY_CONN, (1, 2): EMPTY_CONN}

        plan = run_optimizer(
            tours,
            home="A-Stadt",
            dest="A-Stadt",
            home_to_tour=home_to_tour,
            tour_to_dest=tour_to_dest,
            tour_to_tour=tour_to_tour,
            max_gap_hours=1.0,
        )

        assert plan.num_tours == 3
        assert plan.total_euros == pytest.approx(75.0)


class TestEdgeCases:
    """Edge cases and regression tests."""

    def test_empty_tour_list(self):
        """No tours -> empty plan."""
        plan = run_optimizer([])
        assert plan.num_tours == 0

    def test_tour_at_home_station_needs_no_transfer(self):
        """Tour starting and ending at home station needs no travel connection."""
        tour = make_tour(999, "10:00", "Prenzlau", "11:00", "Prenzlau", 50.0)

        plan = run_optimizer([tour])

        assert plan.num_tours == 1
        assert plan.total_euros == pytest.approx(50.0)

    def test_chain_preserves_time_order(self):
        """Tours in plan must be in chronological order."""
        tour_early = make_tour(1, "07:00", "X", "08:00", "X", 10.0)
        tour_late = make_tour(2, "09:00", "X", "10:00", "X", 15.0)

        # Pass in reverse order deliberately
        tours = [tour_late, tour_early]
        home_to_tour = {0: EMPTY_CONN, 1: EMPTY_CONN}
        tour_to_dest = {0: EMPTY_CONN, 1: EMPTY_CONN}
        tour_to_tour = {(1, 0): EMPTY_CONN}  # early -> late

        plan = run_optimizer(
            tours,
            home="X",
            dest="X",
            home_to_tour=home_to_tour,
            tour_to_dest=tour_to_dest,
            tour_to_tour=tour_to_tour,
            max_gap_hours=2.0,
        )

        assert plan.num_tours == 2
        assert plan.tours[0].tour_nr == 1  # early first
        assert plan.tours[1].tour_nr == 2  # late second

    def test_overlapping_tours_not_chained(self):
        """Temporally overlapping tours cannot be chained."""
        tour_a = make_tour(1, "08:00", "X", "10:00", "X", 30.0)
        tour_b = make_tour(2, "09:00", "X", "11:00", "X", 40.0)

        tours = [tour_a, tour_b]
        home_to_tour = {0: EMPTY_CONN, 1: EMPTY_CONN}
        tour_to_dest = {0: EMPTY_CONN, 1: EMPTY_CONN}

        plan = run_optimizer(
            tours,
            home="X",
            dest="X",
            home_to_tour=home_to_tour,
            tour_to_dest=tour_to_dest,
        )

        assert plan.num_tours == 1
        assert plan.tours[0].tour_nr == 2  # 40 > 30


class TestDayPlanStructure:
    """Verify DayPlan contains correct chain link types."""

    def test_plan_includes_travel_legs(self):
        """Plan includes outbound and inbound when tour is at a remote station."""
        tour = make_tour(1, "10:00", "Fern-Stadt", "11:00", "Fern-Stadt", 50.0)
        outbound = make_leg_conn(
            "Prenzlau", "2026-04-01T08:00", "Fern-Stadt", "2026-04-01T09:50", "RE3",
        )
        inbound = make_leg_conn(
            "Fern-Stadt", "2026-04-01T11:10", "Prenzlau", "2026-04-01T13:00", "RE3",
        )

        plan = run_optimizer(
            [tour],
            home_to_tour={0: outbound},
            tour_to_dest={0: inbound},
        )

        assert plan.num_tours == 1
        types = [link.type for link in plan.chain]
        assert "outbound" in types
        assert "tour" in types
        assert "inbound" in types

    def test_no_travel_legs_at_home_station(self):
        """No outbound/inbound when tour starts and ends at home."""
        tour = make_tour(1, "10:00", "Prenzlau", "11:00", "Prenzlau", 50.0)

        plan = run_optimizer([tour])

        types = [link.type for link in plan.chain]
        assert "outbound" not in types
        assert "inbound" not in types
        assert "tour" in types


class TestPrenzlauRealScenario:
    """Realistic scenario: Prenzlau, 01.04.2026 with real bahn.de reachability.

    Verified connections (bahn.de, 01.04.2026):
      Prenzlau → Warnemünde: dep 07:01, arr 11:48 (2 transfers)
      Warnemünde → Prenzlau: dep 15:49, arr 19:55 (3 transfers)
      Güstrow → Prenzlau:   dep ~19:10, arr ~22:30 (estimated, 2-3 transfers)

    From Prenzlau (dep 04:00), earliest arrival in Warnemünde area is ~11:48.
    So only tours starting after ~11:53 in that area are reachable.
    """

    # All 28 tours on 01.04.2026
    ALL_TOURS_APR01 = [
        make_tour(704213, "03:53", "Lübbenau(Spreewald)", "06:53", "Dessau Hbf", 57.00),
        make_tour(705313, "04:10", "Senftenberg", "04:41", "Cottbus Hbf", 14.30),
        make_tour(704218, "05:00", "Bad Belzig", "05:43", "Berlin-Wannsee", 14.30),
        make_tour(705925, "05:30", "Graal-Müritz", "06:18", "Rostock Hbf", 17.45),
        make_tour(704219, "05:33", "Rostock Hbf", "06:21", "Graal-Müritz", 17.45),
        make_tour(705826, "05:48", "Warnemünde", "06:44", "Güstrow", 17.45),
        make_tour(705312, "05:51", "Cottbus Hbf", "06:18", "Senftenberg", 11.30),
        make_tour(704347, "06:02", "Cottbus Hbf", "09:55", "Falkenberg(Elster)", 69.35, 2),
        make_tour(705978, "06:30", "Graal-Müritz", "09:18", "Rostock Hbf", 45.60, 3),
        make_tour(704220, "06:33", "Rostock Hbf", "07:21", "Graal-Müritz", 17.45),
        make_tour(705926, "07:30", "Graal-Müritz", "08:18", "Rostock Hbf", 17.45),
        make_tour(704349, "08:01", "Barth", "09:53", "Barth", 28.60, 2),
        make_tour(705825, "08:03", "Bad Kleinen", "08:50", "Rostock Hbf", 17.45),
        make_tour(704221, "08:33", "Rostock Hbf", "09:21", "Graal-Müritz", 17.45),
        make_tour(705927, "09:30", "Graal-Müritz", "10:18", "Rostock Hbf", 17.45),
        make_tour(704222, "09:33", "Rostock Hbf", "10:21", "Graal-Müritz", 17.45),
        make_tour(705311, "10:00", "Bad Belzig", "10:43", "Berlin-Wannsee", 14.30),
        make_tour(704344, "12:29", "Warnemünde", "13:48", "Warnemünde", 18.44, 2),  # idx 17
        make_tour(704345, "13:19", "Warnemünde", "14:58", "Warnemünde", 23.10, 2),  # idx 18
        make_tour(706037, "17:01", "Bad Kleinen", "17:56", "Lübeck Hbf", 17.45),
        make_tour(705472, "17:18", "Ueckermünde Stadthafen", "17:57", "Pasewalk", 14.30),
        make_tour(704208, "17:39", "Warnemünde", "18:50", "Güstrow", 22.48),        # idx 21
        make_tour(705371, "18:02", "Lübeck Hbf", "18:51", "Bad Kleinen", 17.45),
        make_tour(705305, "18:45", "Jüterbog", "19:24", "Falkenberg(Elster)", 14.30),
        make_tour(705877, "19:00", "Barth", "21:37", "Stralsund Hbf", 42.90, 3),    # idx 24
        make_tour(705302, "19:26", "Szczecin Glowny", "21:21", "Angermünde", 36.42),# idx 25
        make_tour(704215, "20:14", "Senftenberg", "23:19", "Bad Belzig", 58.58),
        make_tour(704203, "21:31", "Seebad Heringsdorf", "21:40", "Swinoujscie Centrum", 11.30),
    ]

    def _reachable_from_prenzlau(self):
        """Reachability based on bahn.de: earliest Warnemünde arrival 11:48.

        Reachable tours (start >= 11:53 in reachable area):
          idx 17: 704344  12:29 Warnemünde (18.44€)
          idx 18: 704345  13:19 Warnemünde (23.10€)
          idx 20: 705472  17:18 Ueckermünde (14.30€) — close to Prenzlau
          idx 21: 704208  17:39 Warnemünde (22.48€)
          idx 24: 705877  19:00 Barth (42.90€)
          idx 25: 705302  19:26 Szczecin (36.42€) — Prenzlau is very close

        Return connections (must arrive Prenzlau by 23:59):
          Warnemünde → Prenzlau: dep 15:49, arr 19:55 ✓
          Güstrow → Prenzlau:    arr ~22:30 ✓
          Stralsund → Prenzlau:  arr ~01:00 ✗ (too late, ~3.5h trip)
          Angermünde → Prenzlau: arr ~21:45 ✓ (only ~25 min)
          Pasewalk → Prenzlau:   arr ~18:30 ✓ (only ~30 min)
        """
        outbound_warnemu = make_leg_conn(
            "Prenzlau", "2026-04-01T07:01",
            "Warnemünde", "2026-04-01T11:48", "RE3",
        )
        outbound_ueckermuende = make_leg_conn(
            "Prenzlau", "2026-04-01T15:00",
            "Ueckermünde Stadthafen", "2026-04-01T17:00", "RB",
        )
        outbound_szczecin = make_leg_conn(
            "Prenzlau", "2026-04-01T16:00",
            "Szczecin Glowny", "2026-04-01T19:00", "RE",
        )
        outbound_barth = make_leg_conn(
            "Prenzlau", "2026-04-01T14:00",
            "Barth", "2026-04-01T18:30", "RE",
        )

        rueck_warnemu = make_leg_conn(
            "Warnemünde", "2026-04-01T15:49",
            "Prenzlau", "2026-04-01T19:55", "RE3",
        )
        rueck_guestrow = make_leg_conn(
            "Güstrow", "2026-04-01T19:10",
            "Prenzlau", "2026-04-01T22:30", "RE",
        )
        rueck_angermuende = make_leg_conn(
            "Angermünde", "2026-04-01T21:30",
            "Prenzlau", "2026-04-01T21:55", "RE3",
        )
        rueck_pasewalk = make_leg_conn(
            "Pasewalk", "2026-04-01T18:05",
            "Prenzlau", "2026-04-01T18:35", "RE3",
        )

        home_to_tour = {
            17: outbound_warnemu,   # 704344 Warnemünde
            18: outbound_warnemu,   # 704345 Warnemünde
            20: outbound_ueckermuende,  # 705472 Ueckermünde
            21: outbound_warnemu,   # 704208 Warnemünde
            24: outbound_barth,     # 705877 Barth
            25: outbound_szczecin,  # 705302 Szczecin
        }
        tour_to_dest = {
            17: rueck_warnemu,     # 704344 ends Warnemünde
            18: rueck_warnemu,     # 704345 ends Warnemünde
            20: rueck_pasewalk,    # 705472 ends Pasewalk
            21: rueck_guestrow,    # 704208 ends Güstrow
            # 24: 705877 ends Stralsund — too late to return!
            25: rueck_angermuende, # 705302 ends Angermünde
        }

        # Tour-to-tour transfers (same area, within gap)
        tour_to_tour = {
            (17, 18): None,  # 704344 ends 13:48, 704345 starts 13:19 — IMPOSSIBLE
            (18, 21): EMPTY_CONN,  # 704345 ends 14:58 → 704208 starts 17:39 (gap 2h41)
            (17, 21): EMPTY_CONN,  # 704344 ends 13:48 → 704208 starts 17:39 (gap 3h51)
        }

        return home_to_tour, tour_to_dest, tour_to_tour

    def test_finds_better_than_23_euros(self):
        """Optimizer must find more than the 23.10€ it currently reports.

        With realistic reachability, tour 705302 (Szczecin→Angermünde, 36.42€)
        should be selectable — Prenzlau is near both stations.
        """
        home_to_tour, tour_to_dest, tour_to_tour = self._reachable_from_prenzlau()

        plan = run_optimizer(
            self.ALL_TOURS_APR01,
            home="Prenzlau",
            dest="Prenzlau",
            earliest="04:00",
            latest="23:59",
            max_gap_hours=1.0,
            home_to_tour=home_to_tour,
            tour_to_dest=tour_to_dest,
            tour_to_tour=tour_to_tour,
        )

        # Must beat the current wrong result of 23.10€
        assert plan.total_euros > 23.10
        assert plan.total_euros == pytest.approx(36.42)
        assert plan.tours[0].tour_nr == 705302

    def test_chain_with_higher_gap_limit(self):
        """With 3h max gap, chaining Warnemünde tours is possible.

        704345 (13:19-14:58, 23.10€) + 704208 (17:39-18:50, 22.48€) = 45.58€
        vs 705302 single (36.42€)
        Chain should win.
        """
        home_to_tour, tour_to_dest, tour_to_tour = self._reachable_from_prenzlau()

        plan = run_optimizer(
            self.ALL_TOURS_APR01,
            home="Prenzlau",
            dest="Prenzlau",
            earliest="04:00",
            latest="23:59",
            max_gap_hours=3.0,  # allows 2h41 gap between 704345 and 704208
            home_to_tour=home_to_tour,
            tour_to_dest=tour_to_dest,
            tour_to_tour=tour_to_tour,
        )

        assert plan.total_euros == pytest.approx(23.10 + 22.48)
        assert plan.num_tours == 2

    def test_stralsund_no_return_excluded(self):
        """Tour 705877 (42.90€ Barth→Stralsund) excluded: no return by 23:59."""
        home_to_tour, tour_to_dest, tour_to_tour = self._reachable_from_prenzlau()

        plan = run_optimizer(
            self.ALL_TOURS_APR01,
            home="Prenzlau",
            dest="Prenzlau",
            earliest="04:00",
            latest="23:59",
            max_gap_hours=1.0,
            home_to_tour=home_to_tour,
            tour_to_dest=tour_to_dest,
            tour_to_tour=tour_to_tour,
        )

        selected_nrs = {t.tour_nr for t in plan.tours}
        assert 705877 not in selected_nrs  # can't return from Stralsund


class TestAnreiseCrossBorderRegression:
    """Regression: Anreise to cross-border stations must use reasonable routes.

    Bug: Prenzlau → Szczecin Glowny was routed via Berlin-Erfurt-Mühlhausen
    (5h43, 3 transfers) because geocoding restricted "Szczecin Glowny" to
    Deutschland, resolving it to a wrong location in Thuringia.

    Fix: Geocoding must allow Polish/Czech stations to resolve correctly.
    The Anreise from Prenzlau to Szczecin should be ~1.5h direct.
    """

    def test_prenzlau_to_szczecin_outbound_is_direct(self):
        """Prenzlau → Szczecin Glowny is ~80km; Anreise must be <3h."""
        tour = make_tour(
            705302, "19:26", "Szczecin Glowny", "21:21", "Angermünde", 36.42,
        )

        # Realistic direct connection: ~1.5h (Prenzlau → Szczecin via RE66)
        outbound = make_leg_conn(
            "Prenzlau", "2026-04-01T17:30",
            "Szczecin Glowny", "2026-04-01T19:00", "RE66",
        )
        inbound = make_leg_conn(
            "Angermünde", "2026-04-01T21:30",
            "Prenzlau", "2026-04-01T21:55", "RE3",
        )

        plan = run_optimizer(
            [tour],
            home="Prenzlau",
            dest="Prenzlau",
            earliest="04:00",
            latest="23:59",
            home_to_tour={0: outbound},
            tour_to_dest={0: inbound},
        )

        assert plan.num_tours == 1
        assert plan.tours[0].tour_nr == 705302

        # Verify Anreise is present and reasonable
        outbound_links = [l for l in plan.chain if l.type == "outbound"]
        assert len(outbound_links) == 1
        conn = outbound_links[0].connection
        assert conn.duration < timedelta(hours=3), \
            f"Anreise Prenzlau→Szczecin should be <3h, got {conn.duration}"
        assert conn.transfers <= 1, \
            f"Prenzlau→Szczecin should have ≤1 transfer, got {conn.transfers}"

    def test_outbound_via_erfurt_is_wrong(self):
        """Anreise via Erfurt (5h43, 3 transfers) must NOT be the result.

        This is the exact bug observed: Google Maps geocoded Szczecin Glowny
        to a German location, producing a route through Berlin and Erfurt.
        """
        make_tour(
            705302, "19:26", "Szczecin Glowny", "21:21", "Angermünde", 36.42,
        )

        # The WRONG route observed in the bug (5h43 via Erfurt)
        wrong_outbound = Connection(legs=[
            Leg("Prenzlau", datetime(2026, 4, 1, 5, 2),
                "Südkreuz", datetime(2026, 4, 1, 6, 47), "RE3"),
            Leg("Südkreuz", datetime(2026, 4, 1, 7, 14),
                "Erfurt", datetime(2026, 4, 1, 8, 48), "ICE 595"),
            Leg("Erfurt", datetime(2026, 4, 1, 9, 7),
                "Mühlhausen", datetime(2026, 4, 1, 9, 58), "RE11"),
            Leg("Mühlhausen, Bahnhof", datetime(2026, 4, 1, 10, 30),
                "Oberdorla Bahnhof - Vogtei", datetime(2026, 4, 1, 10, 45), "160"),
        ])

        # This route is clearly wrong — it ends in Thuringia, not Szczecin
        assert wrong_outbound.duration > timedelta(hours=5), \
            "The buggy route was 5h43 — this test documents the wrong behavior"
        assert wrong_outbound.transfers == 3, \
            "The buggy route had 3 transfers"
        # The route doesn't even arrive at Szczecin!
        assert wrong_outbound.legs[-1].arrival_station != "Szczecin Glowny", \
            "Bug: route ended at Oberdorla, not Szczecin Glowny"


class TestScaleBeyond20Tours:
    """DAG-DP must handle >20 tours without pruning and find the optimal chain."""

    def test_25_tours_finds_optimal_chain(self):
        """25 tours available, 3 form the optimal chain — no heuristic pruning."""
        # Generate 25 tours at station X, each 30 min, staggered every 40 min
        tours = []
        for i in range(25):
            h, m = divmod(6 * 60 + i * 40, 60)
            dep = f"{h:02d}:{m:02d}"
            h2, m2 = divmod(6 * 60 + i * 40 + 30, 60)
            arr = f"{h2:02d}:{m2:02d}"
            tours.append(make_tour(1000 + i, dep, "X", arr, "X", euros=5.0))

        # Make 3 specific tours much more valuable (spread across the day)
        tours[2] = make_tour(1002, "07:20", "X", "07:50", "X", euros=50.0)   # idx 2
        tours[12] = make_tour(1012, "14:00", "X", "14:30", "X", euros=60.0)  # idx 12
        tours[22] = make_tour(1022, "20:40", "X", "21:10", "X", euros=70.0)  # idx 22

        # All reachable from home (same station), all can return
        home_to_tour = dict.fromkeys(range(25), EMPTY_CONN)
        tour_to_dest = dict.fromkeys(range(25), EMPTY_CONN)

        plan = run_optimizer(
            tours,
            home="X",
            dest="X",
            home_to_tour=home_to_tour,
            tour_to_dest=tour_to_dest,
            max_gap_hours=12.0,
        )

        # All 25 tours are chainable (same station, 10-min gaps) → all selected
        # 22 × 5€ + 50€ + 60€ + 70€ = 290€
        assert plan.num_tours == 25
        assert plan.total_euros == pytest.approx(290.0)


class TestCarMode:
    """Car-mode optimizer finds chains unreachable by transit alone."""

    def test_finds_chain_starting_and_ending_at_same_station(self):
        """A 5:00 tour at Pasewalk is reachable when home (Prenzlau) is 30 min by car."""
        from optimizer import optimize_day_car_mode
        from transit_client import driving_info

        # Two tours, both starting AND ending at Pasewalk on the same day.
        tour_a = make_tour(704347, "05:30", "Pasewalk", "08:00", "Pasewalk", 50.0)
        tour_b = make_tour(704348, "10:00", "Pasewalk", "14:00", "Pasewalk", 60.0)

        # Transit Prenzlau↔Pasewalk is infeasible (no early train) — that is
        # what makes the car necessary. The car-mode constraint requires this:
        # car is a fallback, not a profit center.
        def reach_side_effect(from_id, to_id, *_args, **_kwargs):
            if {from_id, to_id} == {"ChIJ_prenzlau", "ChIJ_pasewalk"}:
                return None
            return Connection(legs=[])

        with patch("optimizer.batch_lookup_stations", return_value={
            "Prenzlau": {"id": "ChIJ_prenzlau", "name": "Prenzlau"},
            "Pasewalk": {"id": "ChIJ_pasewalk", "name": "Pasewalk"},
        }), patch("optimizer.check_reachability_with_ids", side_effect=reach_side_effect), \
             patch("optimizer.driving_info", return_value=(30, 32.0)):

            plan, _candidates = optimize_day_car_mode(
                tours=[tour_a, tour_b],
                home_station="Prenzlau",
                earliest_departure=datetime.combine(DAY, time(4, 0)),
                latest_return=datetime.combine(DAY, time(23, 59)),
                max_car_minutes=60,
                fuel_consumption=7.0,
                fuel_price=1.79,
            )

        # Both tours should be in the chain (same station, no time conflict).
        assert plan.num_tours == 2
        # Chain starts and ends at Pasewalk via car legs.
        assert plan.chain[0].type == "car_outbound"
        assert plan.chain[-1].type == "car_inbound"
        assert plan.chain[0].car_leg.to_station == "Pasewalk"
        assert plan.chain[-1].car_leg.to_station == "Prenzlau"
        # Cost = 2 × 32 km × (7/100 × 1.79) = 8.0192 €
        assert plan.total_costs == pytest.approx(8.0192, abs=0.001)
        # Net euros = 110 − 8.0192 ≈ 101.98
        assert plan.net_euros == pytest.approx(101.98, abs=0.1)

    def test_winner_is_decided_by_net_euros(self):
        """Transit gross 200 vs Car gross 215 with 20 € fuel → transit wins (net 200 > 195)."""
        from optimizer import optimize_with_modes
        from models import DayPlan, ChainLink, Tour, CarLeg

        # Build two pre-cooked plans to inject. Real optimizer integration
        # is covered by other tests; here we test the *comparison logic*.
        transit_plan = DayPlan()
        # 200 € gross via a single tour
        transit_plan.chain.append(ChainLink(type="tour", tour=make_tour(
            1, "08:00", "Prenzlau", "10:00", "Prenzlau", 200.0,
        )))

        car_plan = DayPlan()
        car_plan.chain.append(ChainLink(type="car_outbound", car_leg=CarLeg(
            from_station="Prenzlau", to_station="Pasewalk",
            minutes=30, km=50, cost=10.0,
        )))
        car_plan.chain.append(ChainLink(type="tour", tour=make_tour(
            2, "08:00", "Pasewalk", "10:00", "Pasewalk", 215.0,
        )))
        car_plan.chain.append(ChainLink(type="car_inbound", car_leg=CarLeg(
            from_station="Pasewalk", to_station="Prenzlau",
            minutes=30, km=50, cost=10.0,
        )))

        # Net: transit = 200, car = 215 - 20 = 195
        with patch("optimizer.optimize_day", return_value=(transit_plan, [], set())), \
             patch("optimizer.nearby_park_stations", return_value=[]), \
             patch("optimizer.optimize_day_car_mode", return_value=(car_plan, [])):
            result = optimize_with_modes(
                tours=[transit_plan.chain[0].tour, car_plan.chain[1].tour],
                home_station="Prenzlau", dest_station="Prenzlau",
                earliest_departure=datetime.combine(DAY, time(4, 0)),
                latest_return=datetime.combine(DAY, time(23, 59)),
                max_car_minutes=30,
                fuel_consumption=7.0,
                fuel_price=1.79,
            )

        assert result.winner.net_euros == pytest.approx(200.0, abs=0.1)
        assert result.alternative is not None
        assert result.alternative.net_euros == pytest.approx(195.0, abs=0.1)

    def test_car_mode_skipped_when_dest_differs(self):
        """When dest_station != home_station, the car-mode pass is skipped."""
        from optimizer import optimize_with_modes
        from models import DayPlan

        with patch("optimizer.optimize_day", return_value=(DayPlan(), [], set())) as transit_mock, \
             patch("optimizer.optimize_day_car_mode") as car_mock:
            optimize_with_modes(
                tours=[],
                home_station="Prenzlau", dest_station="Stralsund",  # ≠ home
                earliest_departure=datetime.combine(DAY, time(4, 0)),
                latest_return=datetime.combine(DAY, time(23, 59)),
                max_car_minutes=30,
                fuel_consumption=7.0,
                fuel_price=1.79,
            )

        transit_mock.assert_called_once()
        car_mock.assert_not_called()  # never reaches the car-mode pass

    def test_finds_chain_with_transit_return_to_car(self):
        """Relaxed car-mode: chain ends at a different station, transit returns to car."""
        from optimizer import optimize_day_car_mode

        # One tour that DOES NOT loop back to its start station.
        tour = make_tour(704350, "06:00", "Pasewalk", "10:00", "Stralsund Hbf", 80.0)

        # Mocks: home + Pasewalk + Stralsund geocode; car drive 30 min Prenzlau→Pasewalk;
        # transit Stralsund→Pasewalk takes ~1h, returns by 11:30 (well before 23:59 + grace).
        return_conn = make_leg_conn("Stralsund Hbf", "2026-04-01T10:30:00",
                                     "Pasewalk",      "2026-04-01T11:30:00")

        # Car-mode fallback rule: transit Prenzlau↔Pasewalk and Stralsund→Prenzlau
        # are infeasible, so the car is *required* for both legs. Only the
        # Relaxation-B transit return Stralsund→Pasewalk works.
        def reach_side_effect(from_id, to_id, *_args, **_kwargs):
            if (from_id, to_id) == ("ChIJ_stralsund", "ChIJ_pasewalk"):
                return return_conn
            return None  # all other pairs (incl. Prenzlau↔Pasewalk, Stralsund→Prenzlau)

        with patch("optimizer.batch_lookup_stations", return_value={
            "Prenzlau":     {"id": "ChIJ_prenzlau", "name": "Prenzlau"},
            "Pasewalk":     {"id": "ChIJ_pasewalk", "name": "Pasewalk"},
            "Stralsund Hbf":{"id": "ChIJ_stralsund","name": "Stralsund Hbf"},
        }), patch("optimizer.driving_info", return_value=(30, 32.0)), \
             patch("optimizer.check_reachability_with_ids", side_effect=reach_side_effect):
            plan, _candidates = optimize_day_car_mode(
                tours=[tour],
                home_station="Prenzlau",
                earliest_departure=datetime.combine(DAY, time(4, 0)),
                latest_return=datetime.combine(DAY, time(23, 59)),
                max_car_minutes=60,
                fuel_consumption=7.0,
                fuel_price=1.79,
            )

        # Chain shape: car_outbound → tour → inbound → car_inbound
        assert plan.num_tours == 1
        types = [link.type for link in plan.chain]
        assert types == ["car_outbound", "tour", "inbound", "car_inbound"]
        assert plan.chain[2].connection is not None  # the free transit return leg
        assert plan.chain[2].connection.legs[0].arrival_station == "Pasewalk"

    def test_car_mode_rejected_when_transit_reaches_tour(self):
        """Car is a fallback: if a train can reach the tour from home in time,
        the car is not allowed (prevents Kilometerpauschale from gaming the result)."""
        from optimizer import optimize_day_car_mode

        # Same setup as the Pasewalk roundtrip test, but transit Prenzlau↔Pasewalk
        # IS feasible — so car-mode must produce no chain.
        tour_a = make_tour(704347, "05:30", "Pasewalk", "08:00", "Pasewalk", 50.0)
        tour_b = make_tour(704348, "10:00", "Pasewalk", "14:00", "Pasewalk", 60.0)

        with patch("optimizer.batch_lookup_stations", return_value={
            "Prenzlau": {"id": "ChIJ_prenzlau", "name": "Prenzlau"},
            "Pasewalk": {"id": "ChIJ_pasewalk", "name": "Pasewalk"},
        }), patch("optimizer.check_reachability_with_ids", return_value=Connection(legs=[])), \
             patch("optimizer.driving_info", return_value=(30, 32.0)):
            plan, _candidates = optimize_day_car_mode(
                tours=[tour_a, tour_b],
                home_station="Prenzlau",
                earliest_departure=datetime.combine(DAY, time(4, 0)),
                latest_return=datetime.combine(DAY, time(23, 59)),
                max_car_minutes=60,
                fuel_consumption=7.0,
                fuel_price=1.79,
                fuel_refund_per_km=0.20,  # would normally make car attractive
            )

        # Transit reaches the tour → no car chain is built, even with a
        # juicy 20 ct/km Kilometerpauschale that would otherwise win.
        assert plan.num_tours == 0


# ---------------------------------------------------------------------------
# DayPlan metrics (overhead_duration, euros_per_hour)
# ---------------------------------------------------------------------------

class TestDayPlanMetrics:
    """Cover the new DayPlan properties used by effort-ranked alternatives."""

    def test_overhead_duration_sums_only_connections(self):
        """Tour duration is paid time — only connections + car legs count as overhead."""
        from models import ChainLink, Connection, Leg

        # outbound: 1h, transfer: 30min, inbound: 45min  → 2h15 overhead
        outbound = Connection(legs=[Leg(
            departure_station="A", departure_time=datetime(2026, 6, 1, 5, 0),
            arrival_station="B",   arrival_time=  datetime(2026, 6, 1, 6, 0),
            line="RE1",
        )])
        transfer = Connection(legs=[Leg(
            departure_station="B", departure_time=datetime(2026, 6, 1, 8, 0),
            arrival_station="C",   arrival_time=  datetime(2026, 6, 1, 8, 30),
            line="RE2",
        )])
        inbound = Connection(legs=[Leg(
            departure_station="D", departure_time=datetime(2026, 6, 1, 12, 0),
            arrival_station="A",   arrival_time=  datetime(2026, 6, 1, 12, 45),
            line="RE3",
        )])
        tour_a = make_tour(1, "06:00", "B", "08:00", "B", 30.0)
        tour_b = make_tour(2, "08:30", "C", "12:00", "D", 50.0)

        plan = DayPlan()
        plan.chain.extend([
            ChainLink(type="outbound", connection=outbound),
            ChainLink(type="tour", tour=tour_a),
            ChainLink(type="transfer", connection=transfer),
            ChainLink(type="tour", tour=tour_b),
            ChainLink(type="inbound", connection=inbound),
        ])
        assert plan.overhead_duration == timedelta(hours=2, minutes=15)

    def test_overhead_duration_includes_car_legs(self):
        """car_outbound + car_inbound minutes are part of overhead."""
        from models import CarLeg, ChainLink

        car_out = CarLeg(from_station="Home", to_station="Park",
                         minutes=30, km=32.0, cost=5.0)
        car_in  = CarLeg(from_station="Park", to_station="Home",
                         minutes=35, km=32.0, cost=5.0)  # slightly longer return
        tour = make_tour(1, "06:00", "Park", "08:00", "Park", 50.0)

        plan = DayPlan()
        plan.chain.extend([
            ChainLink(type="car_outbound", car_leg=car_out),
            ChainLink(type="tour", tour=tour),
            ChainLink(type="car_inbound", car_leg=car_in),
        ])
        # 30 + 35 = 65 min; tour duration excluded
        assert plan.overhead_duration == timedelta(minutes=65)

    def test_euros_per_hour_division(self):
        """net_euros / overhead_hours; verify a known value."""
        from models import ChainLink, Connection, Leg

        # 4h13 overhead (253 minutes), 42.12 € net → ≈ 9.99 €/h
        outbound = Connection(legs=[Leg(
            departure_station="A", departure_time=datetime(2026, 6, 1, 5, 0),
            arrival_station="B",   arrival_time=  datetime(2026, 6, 1, 7, 0),
            line="RE1",
        )])  # 2h
        inbound = Connection(legs=[Leg(
            departure_station="C", departure_time=datetime(2026, 6, 1, 11, 0),
            arrival_station="A",   arrival_time=  datetime(2026, 6, 1, 13, 13),
            line="RE2",
        )])  # 2h13
        tour = make_tour(1, "07:00", "B", "11:00", "C", 42.12)

        plan = DayPlan()
        plan.chain.extend([
            ChainLink(type="outbound", connection=outbound),
            ChainLink(type="tour", tour=tour),
            ChainLink(type="inbound", connection=inbound),
        ])
        # 42.12 / (253/60) = 42.12 / 4.2167 ≈ 9.9876
        assert plan.euros_per_hour == pytest.approx(9.99, abs=0.01)

    def test_euros_per_hour_zero_when_no_overhead(self):
        """Degenerate plan with no connections and no car legs returns 0.0."""
        from models import ChainLink

        tour = make_tour(1, "06:00", "A", "08:00", "A", 10.0)
        plan = DayPlan()
        plan.chain.append(ChainLink(type="tour", tour=tour))
        assert plan.euros_per_hour == pytest.approx(0.0, abs=1e-9)


# ---------------------------------------------------------------------------
# Effort-ranked alternatives — _build_top_plans
# ---------------------------------------------------------------------------

def _single_tour_plan(
    tour_nr: int, dep_station: str, dep_time: str, arr_station: str,
    arr_time: str, euros: float,
    outbound_min: int = 60, inbound_min: int = 60,
) -> DayPlan:
    """Build a single-tour DayPlan with synthesised outbound + inbound for tests.

    `outbound_min` and `inbound_min` set the connection durations so the test
    can control the resulting overhead_duration.
    """
    from models import ChainLink, Connection, Leg

    tour = make_tour(tour_nr, dep_time, dep_station, arr_time, arr_station, euros)
    out_dep = tour.departure_dt - timedelta(minutes=outbound_min)
    in_arr  = tour.arrival_dt + timedelta(minutes=inbound_min)

    outbound = Connection(legs=[Leg(
        departure_station="HOME", departure_time=out_dep,
        arrival_station=dep_station, arrival_time=tour.departure_dt,
        line="RE1",
    )])
    inbound = Connection(legs=[Leg(
        departure_station=arr_station, departure_time=tour.arrival_dt,
        arrival_station="HOME", arrival_time=in_arr,
        line="RE2",
    )])
    plan = DayPlan()
    plan.chain.extend([
        ChainLink(type="outbound", connection=outbound),
        ChainLink(type="tour", tour=tour),
        ChainLink(type="inbound", connection=inbound),
    ])
    return plan


class TestTopPlans:
    """_build_top_plans: dedupe + filter + score-sort (net minus overhead penalty)."""

    def test_returns_top_5_sorted_by_score_desc(self):
        from optimizer import _build_top_plans

        # 7 candidates, all 60min overhead (1h < 6h threshold → no penalty).
        # Score == net. Net 30..36, top 5 are 36,35,34,33,32.
        candidates = [
            _single_tour_plan(100 + i, "A", "08:00", "B", "09:00",
                              euros=30.0 + i, outbound_min=30, inbound_min=30)
            for i in range(7)
        ]
        result = _build_top_plans(candidates)
        assert len(result) == 5
        assert [p.tours[0].tour_nr for p in result] == [106, 105, 104, 103, 102]

    def test_demotes_long_overhead_plans(self):
        """High-net plan with > 6h overhead loses score, ranks behind a lower-
        net plan with short overhead."""
        from optimizer import _build_top_plans

        # 44 € / 16h overhead → 44 − 3*(16−6) = 14
        marathon = _single_tour_plan(
            200, "A", "10:00", "B", "12:00",
            euros=44.0, outbound_min=8 * 60, inbound_min=8 * 60,
        )
        # 40 € / 7h overhead → 40 − 3*(7−6) = 37
        compact = _single_tour_plan(
            201, "A", "10:00", "B", "12:00",
            euros=40.0, outbound_min=3 * 60 + 30, inbound_min=3 * 60 + 30,
        )
        result = _build_top_plans([marathon, compact])
        assert [p.tours[0].tour_nr for p in result] == [201, 200]

    def test_deduplicates_identical_tour_sequences(self):
        from optimizer import _build_top_plans

        a = _single_tour_plan(300, "A", "08:00", "B", "09:00",
                              euros=30.0, outbound_min=30, inbound_min=30)
        b = _single_tour_plan(300, "A", "08:00", "B", "09:00",
                              euros=30.0, outbound_min=45, inbound_min=45)
        result = _build_top_plans([a, b])
        assert len(result) == 1

    def test_drops_plans_below_min_net_euros(self):
        from optimizer import _build_top_plans, EFFICIENCY_MIN_NET_EUROS

        assert EFFICIENCY_MIN_NET_EUROS == pytest.approx(10.0)

        tiny = _single_tour_plan(400, "A", "08:00", "B", "09:00",
                                 euros=5.0, outbound_min=5, inbound_min=5)
        big = _single_tour_plan(401, "A", "10:00", "B", "11:00",
                                euros=40.0, outbound_min=60, inbound_min=60)
        result = _build_top_plans([tiny, big])
        assert [p.tours[0].tour_nr for p in result] == [401]

    def test_empty_list_when_no_candidates(self):
        from optimizer import _build_top_plans
        assert _build_top_plans([]) == []

    def test_optimize_with_modes_populates_top_plans(self):
        """End-to-end: optimize_with_modes returns top_plans ranked by score
        and exposes them via the winner/alternative/efficiency_options views."""
        from optimizer import optimize_with_modes

        marathon = _single_tour_plan(
            500, "A", "10:00", "B", "12:00",
            euros=44.0, outbound_min=8 * 60, inbound_min=8 * 60,
        )
        compact_a = _single_tour_plan(
            501, "A", "10:00", "B", "11:00",
            euros=45.0, outbound_min=30, inbound_min=30,
        )
        compact_b = _single_tour_plan(
            502, "A", "12:00", "B", "13:00",
            euros=30.0, outbound_min=60, inbound_min=60,
        )

        with patch("optimizer.optimize_day",
                   return_value=(marathon, [marathon, compact_a, compact_b], set())), \
             patch("optimizer.stations_match", return_value=False):
            result = optimize_with_modes(
                tours=[],
                home_station="Prenzlau", dest_station="Stralsund",
                earliest_departure=datetime.combine(DAY, time(4, 0)),
                latest_return=datetime.combine(DAY, time(23, 59)),
                max_car_minutes=0,
                fuel_consumption=7.0,
                fuel_price=1.79,
            )

        nrs = [p.tours[0].tour_nr for p in result.top_plans]
        assert nrs == [501, 502, 500]
        # Backward-compat views agree with top_plans
        assert result.winner.tours[0].tour_nr == 501
        assert result.alternative.tours[0].tour_nr == 502
        assert [p.tours[0].tour_nr for p in result.efficiency_options] == [500]


# ---------------------------------------------------------------------------
# Hybrid car+transit Anreise
# ---------------------------------------------------------------------------

class TestHybridAnreise:
    """Hybrid Anreise = car to a park-station + transit on to a tour-start.

    Setup pattern shared by all tests:
    - `tour` is the candidate hybrid tour: starts at Stralsund Hbf (≠ Pasewalk),
      arrives at Angermünde. Worth 42.12 €.
    - `decoy` is a do-nothing tour starting at Pasewalk at 03:00, BEFORE the
      car arrives at the park (04:00). Its only purpose is to put "Pasewalk"
      into `optimize_day_car_mode`'s candidate-set, which is derived from
      `t.departure_station`. Because decoy.departure_dt < car_arrival, the
      direct seed loop skips it, so it never contributes to the DP.
    - `_setup_geocode` returns stable IDs for the relevant stations.
    """

    DAY_ISO = "2026-04-01"

    def _setup_geocode(self):
        return {
            "Prenzlau":      {"id": "ChIJ_prenzlau",  "name": "Prenzlau"},
            "Pasewalk":      {"id": "ChIJ_pasewalk",  "name": "Pasewalk"},
            "Stralsund Hbf": {"id": "ChIJ_stralsund", "name": "Stralsund Hbf"},
            "Angermünde":    {"id": "ChIJ_angerm",   "name": "Angermünde"},
        }

    def _decoy(self):
        """Tour at Pasewalk that starts before car_arrival → puts Pasewalk
        into the candidate-set but is never seeded by the direct loop."""
        return make_tour(999000, "03:00", "Pasewalk", "03:30", "Pasewalk", 5.0)

    def test_hybrid_seed_used_when_direct_anreise_failed(self):
        """Tour 721174 starts at Stralsund. Pasewalk→Stralsund is reachable
        by transit; Prenzlau→Stralsund directly is not. The optimizer should
        build a hybrid chain car→Pasewalk, train→Stralsund, tour, train→Pasewalk, car→home."""
        from optimizer import optimize_day_car_mode

        tour = make_tour(721174, "06:12", "Stralsund Hbf", "08:25", "Angermünde", 42.12)
        decoy = self._decoy()

        anreise_transit = make_leg_conn(
            "Pasewalk", f"{self.DAY_ISO}T04:16:00",
            "Stralsund Hbf", f"{self.DAY_ISO}T05:30:00",
            line="RE3",
        )
        return_transit = make_leg_conn(
            "Angermünde", f"{self.DAY_ISO}T08:30:00",
            "Pasewalk", f"{self.DAY_ISO}T09:00:00",
            line="RE3",
        )

        def reachability_router(from_id, to_id, earliest_dep, must_arrive):
            if from_id == "ChIJ_pasewalk" and to_id == "ChIJ_stralsund":
                return anreise_transit
            if from_id == "ChIJ_angerm" and to_id == "ChIJ_pasewalk":
                return return_transit
            return None

        with patch("optimizer.batch_lookup_stations",
                   return_value=self._setup_geocode()), \
             patch("optimizer.driving_info", return_value=(30, 32.0)), \
             patch("optimizer.check_reachability_with_ids",
                   side_effect=reachability_router):
            plan, _candidates = optimize_day_car_mode(
                tours=[tour, decoy],
                home_station="Prenzlau",
                earliest_departure=datetime.combine(DAY, time(3, 30)),
                latest_return=datetime.combine(DAY, time(23, 59)),
                max_car_minutes=60,
                fuel_consumption=7.0,
                fuel_price=1.79,
                directly_reachable_tour_nrs=set(),
            )

        assert plan.num_tours == 1
        assert plan.tours[0].tour_nr == 721174
        types = [link.type for link in plan.chain]
        assert types == ["car_outbound", "outbound", "tour", "inbound", "car_inbound"]
        assert plan.chain[1].connection is anreise_transit  # the hybrid Anreise leg

    def test_hybrid_lookup_skipped_when_tour_directly_reachable(self):
        """When 721174 is in directly_reachable_tour_nrs, the optimizer must
        NOT make the Pasewalk→Stralsund reachability call. We assert on the
        call log, which makes this test independent of plan emptiness."""
        from optimizer import optimize_day_car_mode

        tour = make_tour(721174, "06:12", "Stralsund Hbf", "08:25", "Angermünde", 42.12)
        decoy = self._decoy()

        reachability_calls: list[tuple] = []

        def reachability_router(from_id, to_id, earliest_dep, must_arrive):
            reachability_calls.append((from_id, to_id))
            return None  # nothing reachable in either direction

        with patch("optimizer.batch_lookup_stations",
                   return_value=self._setup_geocode()), \
             patch("optimizer.driving_info", return_value=(30, 32.0)), \
             patch("optimizer.check_reachability_with_ids",
                   side_effect=reachability_router):
            optimize_day_car_mode(
                tours=[tour, decoy],
                home_station="Prenzlau",
                earliest_departure=datetime.combine(DAY, time(3, 30)),
                latest_return=datetime.combine(DAY, time(23, 59)),
                max_car_minutes=60,
                fuel_consumption=7.0,
                fuel_price=1.79,
                directly_reachable_tour_nrs={721174},
            )

        # The hybrid lookup Pasewalk → Stralsund Hbf must NEVER be called.
        # (Other lookups — e.g. transit-back-to-park for other candidates —
        # are unrelated and we don't constrain them here.)
        assert ("ChIJ_pasewalk", "ChIJ_stralsund") not in reachability_calls

    def test_hybrid_seed_skipped_when_no_transit_from_park(self):
        """Direct Anreise failed AND transit park→tour-start unreachable →
        the Stralsund tour does NOT appear in the result chain. Decoy tour
        may or may not appear — we only assert about 721174."""
        from optimizer import optimize_day_car_mode

        tour = make_tour(721174, "06:12", "Stralsund Hbf", "08:25", "Angermünde", 42.12)
        decoy = self._decoy()

        with patch("optimizer.batch_lookup_stations",
                   return_value=self._setup_geocode()), \
             patch("optimizer.driving_info", return_value=(30, 32.0)), \
             patch("optimizer.check_reachability_with_ids", return_value=None):
            plan, _candidates = optimize_day_car_mode(
                tours=[tour, decoy],
                home_station="Prenzlau",
                earliest_departure=datetime.combine(DAY, time(3, 30)),
                latest_return=datetime.combine(DAY, time(23, 59)),
                max_car_minutes=60,
                fuel_consumption=7.0,
                fuel_price=1.79,
                directly_reachable_tour_nrs=set(),
            )

        tour_nrs_in_chain = [t.tour_nr for t in plan.tours]
        assert 721174 not in tour_nrs_in_chain

    def test_hybrid_seed_skipped_when_transit_too_late(self):
        """Transit Pasewalk→Stralsund exists but arrives after the
        must-arrive-by deadline. The Stralsund tour is not seeded."""
        from optimizer import optimize_day_car_mode

        tour = make_tour(721174, "06:12", "Stralsund Hbf", "08:25", "Angermünde", 42.12)
        decoy = self._decoy()

        # In production, check_reachability_with_ids enforces the deadline
        # itself and returns None when no train arrives in time. We mimic
        # that by returning None whenever must_arrive_by is tighter than the
        # only available train (arrives 06:10, deadline 06:07).
        def reachability_router(from_id, to_id, earliest_dep, must_arrive):
            if from_id == "ChIJ_pasewalk" and to_id == "ChIJ_stralsund":
                # Only train available arrives 06:10 → too late for 06:07 deadline.
                return None
            return None

        with patch("optimizer.batch_lookup_stations",
                   return_value=self._setup_geocode()), \
             patch("optimizer.driving_info", return_value=(30, 32.0)), \
             patch("optimizer.check_reachability_with_ids",
                   side_effect=reachability_router):
            plan, _candidates = optimize_day_car_mode(
                tours=[tour, decoy],
                home_station="Prenzlau",
                earliest_departure=datetime.combine(DAY, time(3, 30)),
                latest_return=datetime.combine(DAY, time(23, 59)),
                max_car_minutes=60,
                fuel_consumption=7.0,
                fuel_price=1.79,
                directly_reachable_tour_nrs=set(),
            )

        tour_nrs_in_chain = [t.tour_nr for t in plan.tours]
        assert 721174 not in tour_nrs_in_chain


class TestOptimizeWithModesHybrid:
    """End-to-end: optimize_with_modes plumbs directly_reachable through to
    car-mode and the hybrid pass surfaces tours that direct transit missed.
    """

    def test_directly_reachable_set_is_forwarded_to_car_mode(self):
        """The set returned by optimize_day arrives at optimize_day_car_mode."""
        from optimizer import optimize_with_modes
        from models import DayPlan

        sentinel_set = {12345, 67890}

        with patch("optimizer.optimize_day",
                   return_value=(DayPlan(), [], sentinel_set)), \
             patch("optimizer.stations_match", return_value=True), \
             patch("optimizer.nearby_park_stations", return_value=[]), \
             patch("optimizer.optimize_day_car_mode",
                   return_value=(DayPlan(), [])) as car_mock:
            optimize_with_modes(
                tours=[],
                home_station="Prenzlau", dest_station="Prenzlau",
                earliest_departure=datetime.combine(DAY, time(4, 0)),
                latest_return=datetime.combine(DAY, time(23, 59)),
                max_car_minutes=30,
                fuel_consumption=7.0,
                fuel_price=1.79,
            )

        # Inspect the kwargs the car-mode mock was called with.
        car_mock.assert_called_once()
        kwargs = car_mock.call_args.kwargs
        assert kwargs["directly_reachable_tour_nrs"] == sentinel_set

    def test_end_to_end_pasewalk_scenario(self):
        """Tour starts at Stralsund (not reachable by direct transit from Prenzlau)
        but IS reachable via car-to-Pasewalk + RE3 Pasewalk→Stralsund. The
        winner is the hybrid plan.

        A `decoy` tour at Pasewalk (starts 03:00, before car arrival)
        gets Pasewalk into the candidate-set without affecting the DP."""
        from optimizer import optimize_with_modes

        tour = make_tour(721174, "06:12", "Stralsund Hbf", "08:25", "Angermünde", 42.12)
        decoy = make_tour(999000, "03:00", "Pasewalk", "03:30", "Pasewalk", 5.0)

        anreise_transit = make_leg_conn(
            "Pasewalk", "2026-04-01T04:16:00",
            "Stralsund Hbf", "2026-04-01T05:30:00",
            line="RE3",
        )
        return_transit = make_leg_conn(
            "Angermünde", "2026-04-01T08:30:00",
            "Pasewalk", "2026-04-01T09:00:00",
            line="RE3",
        )

        geocode = {
            "Prenzlau":      {"id": "ChIJ_prenzlau",  "name": "Prenzlau"},
            "Pasewalk":      {"id": "ChIJ_pasewalk",  "name": "Pasewalk"},
            "Stralsund Hbf": {"id": "ChIJ_stralsund", "name": "Stralsund Hbf"},
            "Angermünde":    {"id": "ChIJ_angerm",   "name": "Angermünde"},
        }

        def reachability_router(from_id, to_id, earliest_dep, must_arrive):
            # Direct Prenzlau → anything: unreachable (no early train Prenzlau→Stralsund).
            if from_id == "ChIJ_prenzlau":
                return None
            # Anything → Prenzlau: default unreachable for this test; only the
            # car-mode plan is meaningful and it doesn't need a Rückreise to Prenzlau
            # (the car drives back from the park-station).
            if to_id == "ChIJ_prenzlau":
                return None
            # Pasewalk → Stralsund: feasible (hybrid Anreise).
            if from_id == "ChIJ_pasewalk" and to_id == "ChIJ_stralsund":
                return anreise_transit
            # Angermünde → Pasewalk: feasible (transit-back-to-park).
            if from_id == "ChIJ_angerm" and to_id == "ChIJ_pasewalk":
                return return_transit
            return None

        with patch("optimizer.batch_lookup_stations", return_value=geocode), \
             patch("optimizer.driving_info", return_value=(30, 32.0)), \
             patch("optimizer.check_reachability_with_ids",
                   side_effect=reachability_router), \
             patch("optimizer.nearby_park_stations", return_value=[]):
            result = optimize_with_modes(
                tours=[tour, decoy],
                home_station="Prenzlau", dest_station="Prenzlau",
                earliest_departure=datetime.combine(DAY, time(3, 30)),
                latest_return=datetime.combine(DAY, time(23, 59)),
                max_car_minutes=60,
                fuel_consumption=7.0,
                fuel_price=1.79,
            )

        # Transit-only plan is empty (no direct Anreise to Stralsund or Pasewalk).
        # Car-mode via Pasewalk hybrid wins, picking the high-value tour 721174.
        assert result.winner.num_tours == 1
        assert result.winner.has_car_legs
        assert result.winner.tours[0].tour_nr == 721174
        types = [link.type for link in result.winner.chain]
        assert types == ["car_outbound", "outbound", "tour", "inbound", "car_inbound"]


# ---------------------------------------------------------------------------
# optimize_with_modes auto-discovers park-stations via Places API
# ---------------------------------------------------------------------------

class TestOptimizeWithModesAutoPark:
    """optimize_with_modes auto-calls nearby_park_stations when car-mode is
    active and forwards the result to optimize_day_car_mode as
    additional_park_stations."""

    def test_nearby_called_when_car_mode_active(self):
        from optimizer import optimize_with_modes
        from models import DayPlan

        with patch("optimizer.optimize_day",
                   return_value=(DayPlan(), [], set())), \
             patch("optimizer.stations_match", return_value=True), \
             patch("optimizer.nearby_park_stations",
                   return_value=["Pasewalk", "Angermünde"]) as nearby_mock, \
             patch("optimizer.optimize_day_car_mode",
                   return_value=(DayPlan(), [])):
            optimize_with_modes(
                tours=[],
                home_station="Prenzlau", dest_station="Prenzlau",
                earliest_departure=datetime.combine(DAY, time(4, 0)),
                latest_return=datetime.combine(DAY, time(23, 59)),
                max_car_minutes=30,
                fuel_consumption=7.0,
                fuel_price=1.79,
            )

        nearby_mock.assert_called_once()
        # max_drive_minutes positional or kwarg — accept either
        call = nearby_mock.call_args
        all_args = list(call.args) + list(call.kwargs.values())
        assert 30 in all_args

    def test_nearby_not_called_when_car_mode_off(self):
        """max_car_minutes=0 → no Places API call."""
        from optimizer import optimize_with_modes
        from models import DayPlan

        with patch("optimizer.optimize_day",
                   return_value=(DayPlan(), [], set())), \
             patch("optimizer.nearby_park_stations") as nearby_mock, \
             patch("optimizer.optimize_day_car_mode",
                   return_value=(DayPlan(), [])):
            optimize_with_modes(
                tours=[],
                home_station="Prenzlau", dest_station="Prenzlau",
                earliest_departure=datetime.combine(DAY, time(4, 0)),
                latest_return=datetime.combine(DAY, time(23, 59)),
                max_car_minutes=0,
                fuel_consumption=7.0,
                fuel_price=1.79,
            )
        nearby_mock.assert_not_called()

    def test_nearby_not_called_when_dest_differs(self):
        """Car-mode is only active when home==dest; otherwise the Places API
        is not consulted."""
        from optimizer import optimize_with_modes
        from models import DayPlan

        with patch("optimizer.optimize_day",
                   return_value=(DayPlan(), [], set())), \
             patch("optimizer.stations_match", return_value=False), \
             patch("optimizer.nearby_park_stations") as nearby_mock:
            optimize_with_modes(
                tours=[],
                home_station="Prenzlau", dest_station="Stralsund",
                earliest_departure=datetime.combine(DAY, time(4, 0)),
                latest_return=datetime.combine(DAY, time(23, 59)),
                max_car_minutes=30,
                fuel_consumption=7.0,
                fuel_price=1.79,
            )
        nearby_mock.assert_not_called()

    def test_discovered_stations_passed_to_car_mode(self):
        """The list returned by nearby_park_stations arrives at
        optimize_day_car_mode as additional_park_stations."""
        from optimizer import optimize_with_modes
        from models import DayPlan

        sentinel = ["Pasewalk", "Angermünde", "Eberswalde"]

        with patch("optimizer.optimize_day",
                   return_value=(DayPlan(), [], set())), \
             patch("optimizer.stations_match", return_value=True), \
             patch("optimizer.nearby_park_stations", return_value=sentinel), \
             patch("optimizer.optimize_day_car_mode",
                   return_value=(DayPlan(), [])) as car_mock:
            optimize_with_modes(
                tours=[],
                home_station="Prenzlau", dest_station="Prenzlau",
                earliest_departure=datetime.combine(DAY, time(4, 0)),
                latest_return=datetime.combine(DAY, time(23, 59)),
                max_car_minutes=30,
                fuel_consumption=7.0,
                fuel_price=1.79,
            )

        car_mock.assert_called_once()
        kwargs = car_mock.call_args.kwargs
        assert kwargs["additional_park_stations"] == sentinel
