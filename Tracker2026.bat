@echo off
rem Double-click launcher for Windows. Runs Tracker2026 from source with uv,
rem installing the dependencies on first run.
cd /d "%~dp0"

where uv >nul 2>nul
if errorlevel 1 (
    if exist "%USERPROFILE%\.local\bin\uv.exe" (
        set "PATH=%USERPROFILE%\.local\bin;%PATH%"
    ) else (
        echo uv is not installed. Install it by running this in PowerShell:
        echo.
        echo     powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
        echo.
        echo then double-click Tracker2026.bat again.
        pause
        exit /b 1
    )
)

rem Keep the environment on the local disk rather than in the project folder:
rem installing into a network share can fail (and is slow), and a folder shared
rem between macOS and Windows machines can't hold one environment for both.
if not defined UV_PROJECT_ENVIRONMENT set "UV_PROJECT_ENVIRONMENT=%LOCALAPPDATA%\Tracker2026\venv"

uv run python run_app.py
if errorlevel 1 (
    echo.
    echo Tracker2026 exited with an error ^(see above^).
    pause
)
