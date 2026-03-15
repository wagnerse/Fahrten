"""MyRES 3 HTTP-Client und Excel-Import-Fallback."""

from __future__ import annotations

import json as json_mod
import re
from datetime import date, time, timedelta
from pathlib import Path
from typing import Optional

import logging

from bs4 import BeautifulSoup
import pandas as pd

from models import Tour

logger = logging.getLogger(__name__)


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

    Nutzt curl_cffi mit Chrome-TLS-Fingerprint, da die MyRES-WAF
    Standard-Python-HTTP-Clients per TLS-Fingerprinting blockt.
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
        from curl_cffi.requests import Session

        self._session = Session(impersonate="chrome", verify=False)
        self._logged_in = False
        self._last_error = ""

    def login(self, username: str, password: str) -> bool:
        """Login bei MyRES 3."""
        try:
            resp = self._session.post(
                f"{self.BASE_URL}/index.php?action=login",
                data={"benutzername": username, "passwort": password},
                timeout=30,
            )

            html = resp.text.lower()

            if "action=logout" in html or "abmelden" in html:
                self._logged_in = True
                return True

            if "benutzername" in html and "passwort" in html:
                self._last_error = "Falsche Zugangsdaten"
                return False

            # Session-Cookie vorhanden → vermutlich OK
            cookies = {c.name: c.value for c in self._session.cookies}
            if "rm3_session" in cookies:
                self._logged_in = True
                return True

            self._last_error = "Keine Session erhalten"
            return False

        except Exception as e:
            logger.error("MyRES login failed: %s: %s", type(e).__name__, e)
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
        if not self._logged_in:
            raise RuntimeError("Nicht eingeloggt.")

        # 1. Seite initialisieren (setzt Server-Session-State)
        date_from_str = date_from.strftime("%d.%m.%Y")
        date_to_str = date_to.strftime("%d.%m.%Y")

        init_url = (
            f"{self.BASE_URL}/index.php?action=freie-touren"
            f"&datum_von={date_from_str}&datum_bis={date_to_str}"
        )
        self._session.get(init_url, timeout=30)

        # 2. DataTables AJAX-Request bauen
        state_ids = [self.STATE_IDS[s] for s in states if s in self.STATE_IDS]

        params = {
            "action": "freie-touren",
            "ajax": "1",
            "draw": "1",
            "start": "0",
            "length": "500",
            "heimatbahnhoefe": "0",
            "datum_von": date_from_str,
            "datum_bis": date_to_str,
            "order[0][column]": "4",
            "order[0][dir]": "asc",
            "bundeslaender[]": state_ids,
        }

        for i, name in enumerate(self._DT_COLUMNS):
            params[f"columns[{i}][data]"] = name
            params[f"columns[{i}][name]"] = name
            params[f"columns[{i}][searchable]"] = "false"
            params[f"columns[{i}][orderable]"] = "true"

        resp = self._session.get(
            f"{self.BASE_URL}/index.php",
            params=params,
            headers={
                "X-Requested-With": "XMLHttpRequest",
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "Referer": init_url,
            },
            timeout=30,
        )

        # 3. JSON parsen
        try:
            data = resp.json()
        except (json_mod.JSONDecodeError, ValueError) as e:
            logger.error("MyRES fetch_free_tours JSON parse failed: %s: %s", type(e).__name__, e)
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
        self._session.close()
