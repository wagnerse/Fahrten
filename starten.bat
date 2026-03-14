
@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"

echo ============================================
echo   Fahrtenplaner wird gestartet
...
echo ============================================
echo.

:: --- uv.exe herunterladen falls nicht vorhanden ---
if not exist ".tools\uv.exe" (
    echo uv wird heruntergeladen (einmalig, ~38 MB)...
    mkdir ".tools" 2>nul

    powershell -NoProfile -Command ^
        "$ProgressPreference='SilentlyContinue'; " ^
        "try { " ^
        "  Invoke-WebRequest -Uri 'https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip' " ^
        "    -OutFile '.tools\uv.zip' -UseBasicParsing " ^
        "} catch { " ^
        "  Write-Error $_.Exception.Message; exit 1 " ^
        "}"

    if errorlevel 1 (
        echo.
        echo FEHLER: Download fehlgeschlagen.
        echo Bitte Internetverbindung pruefen und erneut versuchen.
        pause
        exit /b 1
    )

    powershell -NoProfile -Command ^
        "Expand-Archive -Path '.tools\uv.zip' -DestinationPath '.tools\uv-temp' -Force; " ^
        "Copy-Item '.tools\uv-temp\uv.exe' '.tools\uv.exe' -Force; " ^
        "Remove-Item '.tools\uv-temp' -Recurse -Force; " ^
        "Remove-Item '.tools\uv.zip' -Force"

    if not exist ".tools\uv.exe" (
        echo.
        echo FEHLER: uv.exe konnte nicht entpackt werden.
        pause
        exit /b 1
    )

    echo uv erfolgreich installiert.
    echo.
)

:: --- App starten ---
echo Starte Fahrtenplaner (beim ersten Mal werden Pakete installiert)...
echo.

".tools\uv.exe" run --no-project --python 3.12 --with-requirements requirements.txt -- streamlit run fahrtenplaner/app.py --server.address localhost

if errorlevel 1 (
    echo.
    echo ============================================
    echo   FEHLER: Die App konnte nicht gestartet werden.
    echo.
    echo   Tipps:
    echo   - Internetverbindung pruefen
    echo   - Ordner ".tools" loeschen und erneut starten
    echo ============================================
)

pause
