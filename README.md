# Fahrtenplaner

## Starten

**Doppelklick auf `starten.bat`** — fertig.

Beim ersten Start werden automatisch alle Voraussetzungen heruntergeladen (~1 Minute).
Ab dem zweiten Start ist die App in wenigen Sekunden bereit.

Der Browser oeffnet sich automatisch unter `http://localhost:8501`.

## Voraussetzungen

- Windows 10 oder 11
- Internetverbindung (beim ersten Start)

## Entwicklung (macOS/Linux)

Drei Wege, die App lokal zu starten — je nach gewünschter Nähe zur
Windows-Auslieferung:

**1. Browser + Live-Reload** (schnellster Iterations-Loop):

```bash
./dev.sh
```

Bindet `localhost`, aktiviert `runOnSave`, öffnet `http://localhost:8501` im
Standard-Browser. Speichern einer Datei unter `fahrtenplaner/**/*.py` löst
automatisch einen Rerun aus; `st.session_state` bleibt erhalten.

**2. Fenster-Modus + Live-Reload** (entspricht Dads Erlebnis + Hot-Reload):

```bash
uv run python launcher.py --dev
```

Startet die App im selben chromelosen `pywebview`-Fenster wie die ausgelieferte
Windows-`.exe` — aber mit Auto-Reload, sichtbarem Titel-Suffix *„— Dev"* und
Rechtsklick → *Inspect* via Devtools. Der Update-Check wird in diesem Modus
übersprungen.

**3. Fenster-Modus ohne Reload** (reine Produktionsvorschau):

```bash
uv run python launcher.py
```

Genau das, was die `.exe` macht — inklusive Update-Check gegen GitHub
Releases. Nützlich, um vor einem Release die finale Optik zu prüfen.

Tests laufen mocked (keine Live-API-Calls):

```bash
uv run --with pytest pytest tests/
```

## Probleme?

1. Den Ordner `.tools` loeschen
2. `starten.bat` erneut doppelklicken

Damit wird alles frisch heruntergeladen.
