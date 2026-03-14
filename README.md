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

```bash
uv run streamlit run fahrtenplaner/app.py
```

Automatischer Reload bei Dateiänderungen ist über `.streamlit/config.toml` vorkonfiguriert.

## Probleme?

1. Den Ordner `.tools` loeschen
2. `starten.bat` erneut doppelklicken

Damit wird alles frisch heruntergeladen.
