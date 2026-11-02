"""PyInstaller entry point for Tracker2026. Kept at the repo root (rather
than building against app/tracker_gui.py directly) so the `app` package
resolves the same way it does when run via `python -m app.tracker_gui`."""
from app.tracker_gui import main

if __name__ == "__main__":
    main()
