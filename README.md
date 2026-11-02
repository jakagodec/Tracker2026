# Tracker2026

A desktop app for detecting and tracking particles in `.twv` videos, built on
[trackpy](https://soft-matter.github.io/trackpy/) with a PyQt6 interface.

Open a video, tune the detection parameters while looking at the current frame,
track the whole video, then inspect and export the trajectories.

## Features

- Open `.twv` videos (read with a custom [pims](https://github.com/soft-matter/pims) reader, `app/twv.py`).
- Scrub through frames with a slider and frame counter.
- Live particle detection (`trackpy.locate`) on the current frame. You can set the diameter, minimum mass, separation, maximum size and maximum eccentricity. Histograms of mass, size and eccentricity help you pick thresholds.
- Track the full video (`trackpy.link`) with search range, minimum trajectory length and adaptive search. Tracking runs in the background and can be aborted.
- Advanced settings for less common detection and linking options.
- Measure distances on the image by clicking and dragging.
- Plot x(t) and y(t) for every trajectory.
- Export trajectories to a CSV saved next to the video, with the same base name.
- Your parameters are remembered between sessions.
- Numba acceleration is used when available.

## Quick start

### 1. Install uv (once per computer)

The app's Python packages are managed with [uv](https://docs.astral.sh/uv/).
uv also downloads a suitable Python version for you, so you don't need to
install Python separately.

**macOS** (Terminal):

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**Windows** (PowerShell):

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Close and reopen the terminal afterwards so `uv` is on your `PATH`.

### 2. Get the code

```bash
git clone https://github.com/jakagodec/Tracker2026.git
cd Tracker2026
```

(Or use **Code → Download ZIP** on GitHub and unzip it.)

### 3. Run the app

**Double-click the launcher** in the project folder:

| OS      | Launcher                |
|---------|-------------------------|
| macOS   | `Tracker2026.command`   |
| Windows | `Tracker2026.bat`       |

The first launch takes a minute or two while the dependencies are installed.
Later launches start right away.

Or, from a terminal in the project folder:

```bash
uv run python run_app.py
```

> **macOS note:** if you downloaded the ZIP and macOS refuses to open
> `Tracker2026.command` ("unidentified developer"), right-click it and choose
> **Open**. If it isn't executable, run `chmod +x Tracker2026.command` once.

> **Network drives / shared folders:** the launchers install the Python
> environment on the local disk, not in the project folder:
> `~/Library/Caches/Tracker2026/venv` on macOS and
> `%LOCALAPPDATA%\Tracker2026\venv` on Windows. So one copy of the folder on a
> network drive works from both Macs and Windows PCs. To reset a broken
> install, delete that folder and launch again. If you run `uv run` / `uv sync`
> by hand in a project on a network drive, set `UV_PROJECT_ENVIRONMENT` to a
> local path the same way.

## Building a standalone app (optional)

To make an app that runs without uv or Python installed, use
[PyInstaller](https://pyinstaller.org/). Build on the platform you are
targeting: PyInstaller cannot cross-compile.

```bash
uv run --group build pyinstaller Tracker2026.spec --noconfirm --clean
```

The output is:

- **macOS:** `dist/Tracker2026.app`
- **Windows:** `dist/Tracker2026/Tracker2026.exe` (distribute the whole `dist/Tracker2026` folder)

What to know when rebuilding:

- **Code changes** in `app/` need only the command above. `--clean` clears PyInstaller's cache, which avoids stale modules at the cost of a slower build.
- **New third-party packages:** add them with `uv add <package>`. If the package loads plugins or data files that PyInstaller can't discover, add a `collect_all('<package>')` line to `Tracker2026.spec`. The existing lines cover PyQt6, numba, llvmlite, trackpy and pims.
- **Changing the icon:** the spec uses `build_assets/Tracker2026.icns` (macOS) and `build_assets/Tracker2026.ico` (Windows). If you change `app/icon.png`, regenerate those files first. On macOS:
  ```bash
  # after filling build_assets/Tracker2026.iconset/ with resized PNGs (sips -z ...)
  iconutil -c icns build_assets/Tracker2026.iconset -o build_assets/Tracker2026.icns
  ```

## Project layout

```
app/
  tracker_gui.py     # the GUI: main window, dialogs, detection/tracking workers
  twv.py             # pims reader for the .twv video format
  icon.png
run_app.py           # entry point (used by the launchers and PyInstaller)
Tracker2026.command  # macOS double-click launcher
Tracker2026.bat      # Windows double-click launcher
Tracker2026.spec     # PyInstaller build spec (macOS and Windows)
build_assets/        # app icons for the standalone build
pyproject.toml       # dependencies (managed with uv)
uv.lock              # exact pinned versions
```

## Managing dependencies

```bash
uv add <package>          # add a dependency
uv remove <package>       # remove one
uv lock --upgrade         # upgrade everything within the allowed ranges
uv sync                   # bring .venv in line with uv.lock
```

Commit `pyproject.toml` and `uv.lock` together.

## License

[MIT](LICENSE)
