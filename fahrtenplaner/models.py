"""Datenmodelle für den Erhebungsfahrten-Planer."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, time, datetime, timedelta
from typing import Optional


@dataclass
class Tour:
    tour_nr: int
    priority: int
    day_name: str          # "Mi", "Do", "Fr", ...
    date: date
    departure_time: time
    departure_station: str
    arrival_time: time
    arrival_station: str
    num_rides: int         # Anzahl Fahrten
    points: int
    duration: timedelta
    euros: float

    @property
    def departure_dt(self) -> datetime:
        return datetime.combine(self.date, self.departure_time)

    @property
    def arrival_dt(self) -> datetime:
        dt = datetime.combine(self.date, self.arrival_time)
        # Übernacht-Tour: Ankunft nach Mitternacht
        if self.arrival_time < self.departure_time:
            dt += timedelta(days=1)
        return dt

    @property
    def duration_str(self) -> str:
        total_seconds = int(self.duration.total_seconds())
        h, remainder = divmod(total_seconds, 3600)
        m, _ = divmod(remainder, 60)
        return f"{h:02d}:{m:02d}"

    def __str__(self) -> str:
        return (
            f"Tour {self.tour_nr}: {self.departure_station} "
            f"{self.departure_time:%H:%M} → {self.arrival_station} "
            f"{self.arrival_time:%H:%M} ({self.euros:.2f}€)"
        )


@dataclass
class Leg:
    """Ein Abschnitt einer DB-Verbindung."""
    departure_station: str
    departure_time: datetime
    arrival_station: str
    arrival_time: datetime
    line: str                           # z.B. "RE3", "S1", "Bus 123"
    is_replacement_service: bool = False  # Schienenersatzverkehr

    @property
    def duration(self) -> timedelta:
        return self.arrival_time - self.departure_time


@dataclass
class CarLeg:
    """A single-segment car drive between two stations."""
    from_station: str
    to_station: str
    minutes: int        # one-way driving time
    km: float           # one-way distance
    cost: float = 0.0   # fuel cost for THIS leg only (computed by optimizer)


@dataclass
class Connection:
    """Eine DB-Verbindung von A nach B."""
    legs: list[Leg] = field(default_factory=list)

    @property
    def departure_time(self) -> Optional[datetime]:
        return self.legs[0].departure_time if self.legs else None

    @property
    def arrival_time(self) -> Optional[datetime]:
        return self.legs[-1].arrival_time if self.legs else None

    @property
    def duration(self) -> Optional[timedelta]:
        if self.departure_time and self.arrival_time:
            return self.arrival_time - self.departure_time
        return None

    @property
    def transfers(self) -> int:
        return max(0, len(self.legs) - 1)

    @property
    def has_replacement_service(self) -> bool:
        return any(leg.is_replacement_service for leg in self.legs)

    @property
    def duration_str(self) -> str:
        if not self.duration:
            return "?"
        total_seconds = int(self.duration.total_seconds())
        h, remainder = divmod(total_seconds, 3600)
        m, _ = divmod(remainder, 60)
        return f"{h}h{m:02d}"


@dataclass
class ChainLink:
    """Ein Element im Tagesplan: entweder Tour, Transfer oder Auto-Drive."""
    type: str  # "tour" | "transfer" | "outbound" | "inbound" | "car_outbound" | "car_inbound"
    tour: Optional[Tour] = None
    connection: Optional[Connection] = None
    car_leg: Optional[CarLeg] = None
    warning: Optional[str] = None

    @property
    def label(self) -> str:
        labels = {
            "tour":         f"Tour {self.tour.tour_nr}" if self.tour else "Tour",
            "outbound":     "Anreise",
            "inbound":      "Rückreise",
            "car_outbound": "Auto-Anfahrt",
            "car_inbound":  "Auto-Rückfahrt",
        }
        return labels.get(self.type, "Transfer")


@dataclass
class DayPlan:
    """Optimierter Tagesplan mit Tourenkette."""
    chain: list[ChainLink] = field(default_factory=list)

    @property
    def tours(self) -> list[Tour]:
        return [link.tour for link in self.chain if link.tour]

    @property
    def total_euros(self) -> float:
        return sum(t.euros for t in self.tours)

    @property
    def total_costs(self) -> float:
        """Sum of fuel costs across all car legs in the chain."""
        return sum(
            (link.car_leg.cost for link in self.chain if link.car_leg is not None),
            0.0,
        )

    @property
    def overhead_duration(self) -> timedelta:
        """Aufwand: sum of all non-tour durations in the chain.

        Outbound + transfer + inbound connection durations are counted, plus
        any car-leg minutes. Paid tour durations are NOT counted — the user
        considers those productive time, not effort cost.
        """
        total = timedelta(0)
        for link in self.chain:
            if link.connection and link.connection.duration:
                total += link.connection.duration
            if link.car_leg is not None:
                total += timedelta(minutes=link.car_leg.minutes)
        return total

    @property
    def euros_per_hour(self) -> float:
        """Net euros divided by overhead hours. 0.0 when overhead is zero
        (degenerate case — never happens in real chains because outbound
        always carries at least a few minutes of platform time)."""
        hours = self.overhead_duration.total_seconds() / 3600
        return self.net_euros / hours if hours > 0 else 0.0

    @property
    def net_euros(self) -> float:
        """Gross revenue minus fuel cost — the comparison key for winner selection."""
        return self.total_euros - self.total_costs

    @property
    def has_car_legs(self) -> bool:
        """True iff this plan uses car-mode (any car leg in the chain)."""
        return any(link.car_leg is not None for link in self.chain)

    @property
    def num_tours(self) -> int:
        return len(self.tours)

    @property
    def warnings(self) -> list[str]:
        return [link.warning for link in self.chain if link.warning]

    @property
    def time_range(self) -> str:
        if not self.chain:
            return "–"
        first = self.chain[0]
        last = self.chain[-1]
        start = None
        end = None
        if first.connection and first.connection.departure_time:
            start = first.connection.departure_time
        elif first.tour:
            start = first.tour.departure_dt
        if last.connection and last.connection.arrival_time:
            end = last.connection.arrival_time
        elif last.tour:
            end = last.tour.arrival_dt
        if start and end:
            return f"{start:%H:%M} – {end:%H:%M}"
        return "–"


@dataclass
class OptimizationResult:
    """Up to N day-plans, ranked by balanced score (net revenue with soft
    overhead penalty). `top_plans[0]` is the recommendation; the rest are
    alternatives shown in equal cards. `winner`/`alternative`/`efficiency_options`
    are kept as backward-compat views into `top_plans`."""
    winner: DayPlan
    alternative: Optional["DayPlan"] = None
    efficiency_options: list["DayPlan"] = field(default_factory=list)
    top_plans: list["DayPlan"] = field(default_factory=list)
    latest_return_target: Optional[datetime] = None  # user's preferred return time

    @property
    def has_alternative(self) -> bool:
        return self.alternative is not None and self.alternative.num_tours > 0
