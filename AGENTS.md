# AGENTS.md

Guidance for AI coding agents (Claude Code, Codex, Cursor, …) working in this repo.

## What this is

Tracker2026 is a PyQt6 desktop app for particle detection and tracking in
`.twv` videos, built on trackpy. It is a single-window GUI. There is no
server, no CLI and no test suite.

## Layout

- `app/tracker_gui.py` holds the whole GUI: `MainWindow`, the dialogs (`AdvancedSettingsDialog`, `HistogramDialog`, `TrajectoryPlotDialog`), `SliderSpin`, the matplotlib canvas, the `QThread` workers for full-video tracking, and `main()`.
- `app/twv.py` holds `TWVReader`, a `pims.FramesSequence` that parses the binary `.twv` format with `ctypes` structures.
- `run_app.py` is the entry point. It sits at the repo root so that `app` imports the same way from source and inside PyInstaller.
- `Tracker2026.spec` is the PyInstaller spec. One spec serves both macOS (`.app` bundle, `.icns`) and Windows (folder build, `.ico`).
- `Tracker2026.command` and `Tracker2026.bat` are the double-click launchers. They run `uv run python run_app.py` with `UV_PROJECT_ENVIRONMENT` pointed at a local per-user venv (`~/Library/Caches/Tracker2026/venv`, `%LOCALAPPDATA%\Tracker2026\venv`). The project often lives on an SMB network share, where installing a venv fails.

## Commands

```bash
uv sync                                     # install deps into .venv
uv run python run_app.py                    # run the app
uv run --group build pyinstaller Tracker2026.spec --noconfirm --clean   # standalone build
```

Smoke test without a display (catches import and construction errors):

```bash
QT_QPA_PLATFORM=offscreen uv run python -c "from PyQt6.QtWidgets import QApplication; import app.tracker_gui as g; a=QApplication([]); g.MainWindow()"
```

Real behaviour (detection, tracking, plots) needs a `.twv` file and a manual
run of the GUI. Say so when you can't verify a change that way.

## Conventions and constraints

- Dependencies are managed with **uv** only. Use `uv add` / `uv remove` and commit `pyproject.toml` and `uv.lock` together. Don't add `requirements.txt` or call pip.
- The app must keep working **frozen** (PyInstaller) on both macOS and Windows:
  - Keep `multiprocessing.freeze_support()` at the top of `main()`, because trackpy uses a process pool.
  - If a new dependency loads plugins or data dynamically, add `collect_all(...)` for it in `Tracker2026.spec`.
  - Don't rely on `__file__`-relative paths without handling `sys._MEIPASS`.
- Keep numba optional. Code paths use `LOCATE_ENGINE` / `LINK_STRATEGY` and fall back to trackpy's `"auto"` when numba is missing.
- Persisted settings use `QSettings(SETTINGS_ORG, SETTINGS_APP)`. Don't change those identifiers, or users lose their saved parameters.
- Long-running work (full-video tracking) runs in `QThread` workers. Never block the GUI thread.
- Style: plain PyQt6 widgets, docstrings on classes and non-trivial methods, and comments that explain *why*. Match the existing code.
- Never commit data (`*.twv`, exported `*.csv`), virtual environments or `build/` / `dist/`.
