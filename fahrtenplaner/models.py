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
    """Ein Element im Tagesplan: entweder Tour oder Transfer."""
    type: str  # "tour", "transfer", "anreise", "rückreise"
    tour: Optional[Tour] = None
    connection: Optional[Connection] = None
    warning: Optional[str] = None

    @property
    def label(self) -> str:
        if self.type == "tour" and self.tour:
            return f"Tour {self.tour.tour_nr}"
        if self.type == "anreise":
            return "Anreise"
        if self.type == "rückreise":
            return "Rückreise"
        return "Transfer"


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
