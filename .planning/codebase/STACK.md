# Technology Stack

**Analysis Date:** 2025-03-14

## Languages

**Primary:**
- Python 3.13 - Main application language
- Python 3.12 - Supported runtime (specified in `starten.bat`)

**Secondary:**
- HTML/CSS - Inline styling in Streamlit for UI customization
- Batch scripting - Windows launcher (`starten.bat`)

## Runtime

**Environment:**
- Python 3.13+ (macOS/Linux development), 3.12 (Windows via `uv`)

**Package Manager:**
- `uv` - Fast Python package manager/installer (version not specified, auto-downloaded from GitHub)
- Lockfile: `requirements.txt` (simple requirements list, no lock file)

## Frameworks

**Core:**
- Streamlit 1.30.0+ - Web framework for data app UI and session management

**Data Processing:**
- pandas 2.0.0+ - Data manipulation and DataFrame operations
- openpyxl 3.1.0+ - Excel file reading/writing (for tour data import)

**HTTP Client:**
- httpx 0.25.0+ - Async HTTP client for API calls with timeout management

**Web Scraping:**
- beautifulsoup4 4.12.0+ - HTML parsing (prepared but not actively used in shown code)

**Testing:**
- No testing framework detected

**Build/Dev:**
- uv - Dependency management and runtime bootstrapping

## Key Dependencies

**Critical:**
- streamlit - Web UI, session state management, Streamlit caching decorators
- pandas - Excel import, data filtering, DataFrame operations
- httpx - API client for DB transport.rest API and MyRES HTTP calls
- openpyxl - Excel file parsing for tour data

**Infrastructure:**
- No external databases or ORMs
- No async runtime (uses synchronous httpx client)
- No logging framework (prints to console/Streamlit UI)

## Configuration

**Environment:**
- No `.env` file detected - configuration via Streamlit sidebar UI inputs
- No environment variables required for core functionality
- MyRES credentials: entered via Streamlit sidebar text inputs (username/password)

**Build:**
- `pyproject.toml` - Minimal project metadata, no build configuration
- `starten.bat` - Windows launcher that:
  1. Downloads `uv.exe` from GitHub (38 MB) if not present
  2. Runs: `uv run --no-project --python 3.12 --with-requirements requirements.txt -- streamlit run fahrtenplaner/app.py`
- Streamlit config: `.streamlit/config.toml` contains theme colors and headless mode setting

## Platform Requirements

**Development:**
- Windows 10/11 (primary target via `starten.bat`)
- macOS/Linux (supported but no automatic launcher)
- Internet connection (required for first-time `uv` download and API access)

**Production:**
- Windows 10/11 for end users
- Localhost server (Streamlit default at `http://localhost:8501`)
- Network access to:
  - MyRES API: `https://res.ivv-berlin.de` (tour data)
  - DB Transport.rest API: `https://v6.db.transport.rest` (journey connections)

## Package Management

**Installation:**
- `requirements.txt` specifies 5 core dependencies with version constraints
- No `pip` lock file - uses semantic versioning for compatibility
- `uv` automatically downloads and manages Python 3.12 on Windows
- Virtual environment: `.venv/` directory (present on disk, isolated from system Python)

## Startup Flow

**Windows (primary):**
1. `starten.bat` executes
2. Checks for `.tools/uv.exe`, downloads if missing
3. Invokes: `uv run --python 3.12 --with-requirements requirements.txt -- streamlit run fahrtenplaner/app.py`
4. Browser opens automatically to `http://localhost:8501`

**Unix-like (development):**
1. Activate venv: `. .venv/bin/activate`
2. Run: `streamlit run fahrtenplaner/app.py`

---

*Stack analysis: 2025-03-14*
