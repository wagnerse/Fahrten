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
        return optimize_day(
            tours=tours,
            home_station=home,
            dest_station=dest,
            earliest_departure=datetime.combine(DAY, time(h1, m1)),
            latest_return=datetime.combine(DAY, time(h2, m2)),
            max_transfer_gap_hours=max_gap_hours,
        )


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
        """Plan includes anreise and rueckreise when tour is at a remote station."""
        tour = make_tour(1, "10:00", "Fern-Stadt", "11:00", "Fern-Stadt", 50.0)
        anreise = make_leg_conn(
            "Prenzlau", "2026-04-01T08:00", "Fern-Stadt", "2026-04-01T09:50", "RE3",
        )
        rueckreise = make_leg_conn(
            "Fern-Stadt", "2026-04-01T11:10", "Prenzlau", "2026-04-01T13:00", "RE3",
        )

        plan = run_optimizer(
            [tour],
            home_to_tour={0: anreise},
            tour_to_dest={0: rueckreise},
        )

        assert plan.num_tours == 1
        types = [link.type for link in plan.chain]
        assert "anreise" in types
        assert "tour" in types
        assert "rückreise" in types

    def test_no_travel_legs_at_home_station(self):
        """No anreise/rueckreise when tour starts and ends at home."""
        tour = make_tour(1, "10:00", "Prenzlau", "11:00", "Prenzlau", 50.0)

        plan = run_optimizer([tour])

        types = [link.type for link in plan.chain]
        assert "anreise" not in types
        assert "rückreise" not in types
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
        anreise_warnemu = make_leg_conn(
            "Prenzlau", "2026-04-01T07:01",
            "Warnemünde", "2026-04-01T11:48", "RE3",
        )
        anreise_ueckermuende = make_leg_conn(
            "Prenzlau", "2026-04-01T15:00",
            "Ueckermünde Stadthafen", "2026-04-01T17:00", "RB",
        )
        anreise_szczecin = make_leg_conn(
            "Prenzlau", "2026-04-01T16:00",
            "Szczecin Glowny", "2026-04-01T19:00", "RE",
        )
        anreise_barth = make_leg_conn(
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
            17: anreise_warnemu,   # 704344 Warnemünde
            18: anreise_warnemu,   # 704345 Warnemünde
            20: anreise_ueckermuende,  # 705472 Ueckermünde
            21: anreise_warnemu,   # 704208 Warnemünde
            24: anreise_barth,     # 705877 Barth
            25: anreise_szczecin,  # 705302 Szczecin
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

    def test_prenzlau_to_szczecin_anreise_is_direct(self):
        """Prenzlau → Szczecin Glowny is ~80km; Anreise must be <3h."""
        tour = make_tour(
            705302, "19:26", "Szczecin Glowny", "21:21", "Angermünde", 36.42,
        )

        # Realistic direct connection: ~1.5h (Prenzlau → Szczecin via RE66)
        anreise = make_leg_conn(
            "Prenzlau", "2026-04-01T17:30",
            "Szczecin Glowny", "2026-04-01T19:00", "RE66",
        )
        rueckreise = make_leg_conn(
            "Angermünde", "2026-04-01T21:30",
            "Prenzlau", "2026-04-01T21:55", "RE3",
        )

        plan = run_optimizer(
            [tour],
            home="Prenzlau",
            dest="Prenzlau",
            earliest="04:00",
            latest="23:59",
            home_to_tour={0: anreise},
            tour_to_dest={0: rueckreise},
        )

        assert plan.num_tours == 1
        assert plan.tours[0].tour_nr == 705302

        # Verify Anreise is present and reasonable
        anreise_links = [l for l in plan.chain if l.type == "anreise"]
        assert len(anreise_links) == 1
        conn = anreise_links[0].connection
        assert conn.duration < timedelta(hours=3), \
            f"Anreise Prenzlau→Szczecin should be <3h, got {conn.duration}"
        assert conn.transfers <= 1, \
            f"Prenzlau→Szczecin should have ≤1 transfer, got {conn.transfers}"

    def test_anreise_via_erfurt_is_wrong(self):
        """Anreise via Erfurt (5h43, 3 transfers) must NOT be the result.

        This is the exact bug observed: Google Maps geocoded Szczecin Glowny
        to a German location, producing a route through Berlin and Erfurt.
        """
        tour = make_tour(
            705302, "19:26", "Szczecin Glowny", "21:21", "Angermünde", 36.42,
        )

        # The WRONG route observed in the bug (5h43 via Erfurt)
        wrong_anreise = Connection(legs=[
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
        assert wrong_anreise.duration > timedelta(hours=5), \
            "The buggy route was 5h43 — this test documents the wrong behavior"
        assert wrong_anreise.transfers == 3, \
            "The buggy route had 3 transfers"
        # The route doesn't even arrive at Szczecin!
        assert wrong_anreise.legs[-1].arrival_station != "Szczecin Glowny", \
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
        home_to_tour = {i: EMPTY_CONN for i in range(25)}
        tour_to_dest = {i: EMPTY_CONN for i in range(25)}

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
