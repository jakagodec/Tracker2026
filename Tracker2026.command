#!/bin/bash
# Double-click launcher for macOS. Runs Tracker2026 from source with uv,
# installing the dependencies on first run.
cd "$(dirname "$0")" || exit 1

# Finder-launched Terminal sessions don't always load the shell profile, so
# add uv's usual install locations explicitly.
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

if ! command -v uv >/dev/null 2>&1; then
    echo "uv is not installed. Install it by running this in Terminal:"
    echo
    echo "    curl -LsSf https://astral.sh/uv/install.sh | sh"
    echo
    echo "then double-click Tracker2026.command again."
    read -r -p "Press Enter to close..."
    exit 1
fi

# Keep the environment on the local disk rather than in the project folder:
# installing into a network share can fail (and is slow), and a folder shared
# between macOS and Windows machines can't hold one environment for both.
export UV_PROJECT_ENVIRONMENT="${UV_PROJECT_ENVIRONMENT:-$HOME/Library/Caches/Tracker2026/venv}"

if ! uv run python run_app.py; then
    echo
    echo "Tracker2026 exited with an error (see above)."
    read -r -p "Press Enter to close..."
fi
