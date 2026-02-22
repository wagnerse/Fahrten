"""MyRES 3 HTTP-Client und Excel-Import-Fallback."""

from __future__ import annotations

import re
from datetime import date, time, timedelta
from pathlib import Path
from typing import Optional

import httpx
from bs4 import BeautifulSoup
import pandas as pd

from models import Tour


# ---------------------------------------------------------------------------
# Excel-Import (primärer Pfad - zuverlässig und offline-fähig)
# ---------------------------------------------------------------------------

def load_tours_from_excel(path: str | Path) -> list[Tour]:
    """Lädt Touren aus einer MyRES-Export-Excel-Datei."""
    df = pd.read_excel(path)

    # Spaltennamen normalisieren
    col_map = _detect_columns(df.columns.tolist())
    tours: list[Tour] = []

    for _, row in df.iterrows():
        try:
            tour = _row_to_tour(row, col_map)
            if tour:
                tours.append(tour)
        except Exception:
            continue  # Fehlerhafte Zeilen überspringen

    return tours


def _detect_columns(cols: list[str]) -> dict[str, str]:
    """Erkennt Spalten anhand typischer MyRES-Header."""
    mapping = {}
    patterns = {
        "tour_nr": r"tour.?nr|^nummer$|^nr$",
        "priority": r"prio",
        "day_name": r"^tag$",
        "date": r"datum|^date$",
        "departure_time": r"^ab$|abfahrt|^departure$",
        "departure_station": r"start|^von$|^from$|abfahrts?bahnhof",
        "arrival_time": r"^an$|ankunft|^arrival$",
        "arrival_station": r"ziel|^nach$|^to$|zielbahnhof|ankunfts?bahnhof",
        "num_rides": r"fahrt|ride",
        "points": r"punkt|point",
        "duration": r"dauer|duration",
        "euros": r"euro|€|preis|price",
    }
    for key, pattern in patterns.items():
        for col in cols:
            if re.search(pattern, str(col), re.IGNORECASE):
                mapping[key] = col
                break
    return mapping


def _parse_time(val) -> Optional[time]:
    """Parst Zeitwerte aus verschiedenen Formaten."""
    if pd.isna(val):
        return None
    s = str(val).strip()
    # "HH:MM" oder "HH:MM:SS"
    m = re.match(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", s)
    if m:
        return time(int(m.group(1)), int(m.group(2)))
    return None


def _parse_duration(val) -> timedelta:
    """Parst Dauer-Werte in timedelta."""
    if pd.isna(val):
        return timedelta()
    s = str(val).strip()
    m = re.match(r"(\d{1,2}):(\d{2})", s)
    if m:
        return timedelta(hours=int(m.group(1)), minutes=int(m.group(2)))
    return timedelta()


def _parse_date(val) -> Optional[date]:
    """Parst Datumswerte."""
    if pd.isna(val):
        return None
    if isinstance(val, date):
        return val
    s = str(val).strip()
    # DD.MM.YYYY
    m = re.match(r"(\d{2})\.(\d{2})\.(\d{4})", s)
    if m:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    return None


def _row_to_tour(row, col_map: dict[str, str]) -> Optional[Tour]:
    """Konvertiert eine DataFrame-Zeile in ein Tour-Objekt."""
    def get(key: str, default=None):
        col = col_map.get(key)
        if col is None:
            return default
        val = row.get(col)
        if pd.isna(val):
            return default
        return val

    tour_nr = get("tour_nr")
    if tour_nr is None:
        return None

    dep_time = _parse_time(get("departure_time"))
    arr_time = _parse_time(get("arrival_time"))
    tour_date = _parse_date(get("date"))

    if not dep_time or not arr_time or not tour_date:
        return None

    return Tour(
        tour_nr=int(tour_nr),
        priority=int(get("priority", 1)),
        day_name=str(get("day_name", "")),
        date=tour_date,
        departure_time=dep_time,
        departure_station=str(get("departure_station", "")),
        arrival_time=arr_time,
        arrival_station=str(get("arrival_station", "")),
        num_rides=int(get("num_rides", 1)),
        points=int(get("points", 0)),
        duration=_parse_duration(get("duration")),
        euros=float(get("euros", 0)),
    )


# ---------------------------------------------------------------------------
# MyRES HTTP-Client (für Live-Zugriff)
# ---------------------------------------------------------------------------

class MyRESClient:
    """HTTP-Client für MyRES 3 (res.ivv-berlin.de).

    Nutzt curl statt httpx, da die MyRES-WAF Python-HTTP-Clients
    per TLS-Fingerprinting blockt.
    """

    BASE_URL = "https://res.ivv-berlin.de"

    # Bundesland-IDs in MyRES
    STATE_IDS = {
        "Brandenburg": "4",
        "Mecklenburg-Vorpommern": "8",
        "Sachsen": "13",
        "Sachsen-Anhalt": "14",
        "Thüringen": "16",
        "S-Bahn Mitteldeutschland": "17",
    }

    # DataTables Spalten-Namen (für Server-Side Processing)
    _DT_COLUMNS = [
        "Prioritaet", "TourNr", "Markierung", "Prio", "Datum",
        "tAb", "BhfAb", "tAn", "BhfAn", "AnzFahrten",
        "ErhebLeit", "Bonuspunkte", "Dauer", "Verguetung",
    ]

    def __init__(self):
        self._session: Optional[str] = None
        self._logged_in = False
        self._last_error = ""

    def _curl(self, url: str, *, post_data: Optional[str] = None,
              extra_headers: Optional[list[str]] = None) -> Optional[str]:
        """Führt einen curl-Request aus und gibt den Response-Body zurück."""
        import subprocess

        cmd = ["curl", "-sk", "--globoff", "-L", "--max-time", "30"]

        if self._session:
            cmd += ["-b", f"rm3_session={self._session}"]

        # Session-Cookie aus Response extrahieren
        cmd += ["-c", "-"]

        if post_data:
            cmd += ["-d", post_data]

        for h in (extra_headers or []):
            cmd += ["-H", h]

        cmd.append(url)

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=35)

        # Session-Cookie aktualisieren
        for line in result.stdout.split("\n"):
            if "rm3_session" in line:
                parts = line.split()
                if parts:
                    self._session = parts[-1]

        # Cookie-Jar Zeilen entfernen, nur HTTP-Body zurückgeben
        lines = result.stdout.split("\n")
        body_lines = []
        in_body = False
        for line in lines:
            if line.startswith(("# ", "#HttpOnly_", "\t", ".")) and not in_body:
                continue  # Cookie-Jar Zeile
            if not in_body and not line.strip():
                continue  # Leerzeile zwischen Cookie-Jar und Body
            in_body = True
            body_lines.append(line)

        return "\n".join(body_lines)

    def login(self, username: str, password: str) -> bool:
        """Login bei MyRES 3."""
        import subprocess
        try:
            # Login via curl - Session-Cookie wird automatisch gesetzt
            cmd = [
                "curl", "-sk", "-c", "-", "-L",
                "-d", f"benutzername={username}&passwort={password}",
                f"{self.BASE_URL}/index.php?action=login",
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

            # Session extrahieren
            for line in result.stdout.split("\n"):
                if "rm3_session" in line:
                    self._session = line.split()[-1]

            if not self._session:
                self._last_error = "Keine Session erhalten"
                return False

            # Prüfe ob Login erfolgreich
            html = result.stdout.lower()
            if "action=logout" in html or "abmelden" in html:
                self._logged_in = True
                return True

            if "benutzername" in html and "passwort" in html:
                self._last_error = "Falsche Zugangsdaten"
                return False

            # Session erhalten → vermutlich OK
            self._logged_in = True
            return True

        except Exception as e:
            self._last_error = str(e)
            return False

    @property
    def last_error(self) -> str:
        return self._last_error

    def fetch_free_tours(
        self,
        states: list[str],
        date_from: date,
        date_to: date,
    ) -> list[Tour]:
        """Lädt freie Touren aus MyRES via DataTables JSON-API."""
        import subprocess, json as json_mod

        if not self._logged_in or not self._session:
            raise RuntimeError("Nicht eingeloggt.")

        # 1. Seite initialisieren (setzt Server-Session-State)
        date_from_str = date_from.strftime("%d.%m.%Y")
        date_to_str = date_to.strftime("%d.%m.%Y")

        init_url = (
            f"{self.BASE_URL}/index.php?action=freie-touren"
            f"&datum_von={date_from_str}&datum_bis={date_to_str}"
        )
        subprocess.run(
            ["curl", "-sk", "-b", f"rm3_session={self._session}", init_url],
            capture_output=True, timeout=30,
        )

        # 2. DataTables AJAX-Request bauen
        state_ids = [self.STATE_IDS[s] for s in states if s in self.STATE_IDS]

        params = [
            "action=freie-touren",
            "ajax=1",
            "draw=1",
            "start=0",
            "length=500",  # Alle auf einmal
            "heimatbahnhoefe=0",
            f"datum_von={date_from_str}",
            f"datum_bis={date_to_str}",
            "order[0][column]=4",  # Sortiere nach Datum
            "order[0][dir]=asc",
        ]

        for sid in state_ids:
            params.append(f"bundeslaender[]={sid}")

        for i, name in enumerate(self._DT_COLUMNS):
            params.append(f"columns[{i}][data]={name}")
            params.append(f"columns[{i}][name]={name}")
            params.append(f"columns[{i}][searchable]=false")
            params.append(f"columns[{i}][orderable]=true")

        url = f"{self.BASE_URL}/index.php?" + "&".join(params)

        result = subprocess.run(
            [
                "curl", "-sk", "--globoff",
                "-b", f"rm3_session={self._session}",
                "-H", "X-Requested-With: XMLHttpRequest",
                "-H", "Accept: application/json, text/javascript, */*; q=0.01",
                "-H", f"Referer: {init_url}",
                url,
            ],
            capture_output=True, text=True, timeout=30,
        )

        # 3. JSON parsen
        try:
            data = json_mod.loads(result.stdout.strip())
        except (json_mod.JSONDecodeError, ValueError):
            self._last_error = "Ungültige Antwort vom Server"
            return []

        records = data.get("data", [])
        return [t for t in (self._json_to_tour(r) for r in records) if t is not None]

    def _json_to_tour(self, rec: dict) -> Optional[Tour]:
        """Konvertiert einen JSON-Record in ein Tour-Objekt."""
        try:
            dep_time = _parse_time(rec.get("tAb"))
            arr_time = _parse_time(rec.get("tAn"))
            tour_date = _parse_date(rec.get("Datum"))

            if not dep_time or not arr_time or not tour_date:
                return None

            # Vergütung: "19,20&nbsp;€" → 19.20
            verg_str = rec.get("Verguetung", "0")
            verg_str = verg_str.replace("&nbsp;", "").replace("€", "").replace(",", ".").strip()
            euros = float(verg_str) if verg_str else 0

            return Tour(
                tour_nr=int(rec["TourNr"]),
                priority=int(rec.get("Prio", 1)),
                day_name=rec.get("Wochentag", ""),
                date=tour_date,
                departure_time=dep_time,
                departure_station=rec.get("BhfAb", ""),
                arrival_time=arr_time,
                arrival_station=rec.get("BhfAn", ""),
                num_rides=int(rec.get("AnzFahrten", 1)),
                points=int(rec.get("Bonuspunkte", 0)),
                duration=_parse_duration(rec.get("Dauer")),
                euros=euros,
            )
        except Exception:
            return None

    def close(self):
        pass
