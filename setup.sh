#!/usr/bin/env bash
# setup.sh — Environment setup script for the Trivia Quiz Game
# Creates a Python virtual environment in .venv and installs dependencies.
# Requires Python 3.10 or higher.

set -e  # Exit immediately on any error

# ---------------------------------------------------------------------------
# 1. Locate a Python interpreter
# ---------------------------------------------------------------------------
# Prefer python3; fall back to python if python3 is not on PATH.
if command -v python3 &>/dev/null; then
    PYTHON=$(command -v python3)
elif command -v python &>/dev/null; then
    PYTHON=$(command -v python)
else
    echo "Error: No Python interpreter found. Please install Python 3.10 or higher."
    exit 1
fi

# ---------------------------------------------------------------------------
# 2. Check Python version >= 3.10
# ---------------------------------------------------------------------------
PY_MAJOR=$("$PYTHON" -c "import sys; print(sys.version_info.major)")
PY_MINOR=$("$PYTHON" -c "import sys; print(sys.version_info.minor)")
PY_VERSION="${PY_MAJOR}.${PY_MINOR}"

if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 10 ]; }; then
    echo "Error: Python 3.10 or higher is required. Found Python ${PY_VERSION}."
    echo "Please upgrade your Python installation and re-run this script."
    exit 1
fi

echo "Python ${PY_VERSION} detected."

# ---------------------------------------------------------------------------
# 3. Create the virtual environment in .venv (project root)
# ---------------------------------------------------------------------------
if [ -d ".venv" ]; then
    echo ".venv already exists — skipping creation."
else
    echo "Creating virtual environment in .venv ..."
    "$PYTHON" -m venv .venv
    echo "Virtual environment created."
fi

# ---------------------------------------------------------------------------
# 4. Upgrade pip silently, then install dependencies from requirements.txt
# ---------------------------------------------------------------------------
echo "Upgrading pip ..."
.venv/bin/pip install --upgrade pip --quiet

echo "Installing dependencies from requirements.txt ..."
.venv/bin/pip install -r requirements.txt

# ---------------------------------------------------------------------------
# 5. Verify tkinter is available (it ships with Python but can be missing
#    on some Linux distros where it must be installed separately).
# ---------------------------------------------------------------------------
if ! .venv/bin/python -c "import tkinter" &>/dev/null; then
    echo ""
    echo "Warning: tkinter is not available in this Python installation."
    echo "On Debian/Ubuntu, install it with:"
    echo "  sudo apt-get install python3-tk"
    echo "On Fedora/RHEL:"
    echo "  sudo dnf install python3-tkinter"
    echo "On macOS (Homebrew):"
    echo "  brew install python-tk"
    echo ""
fi

# ---------------------------------------------------------------------------
# 6. Done
# ---------------------------------------------------------------------------
echo ""
echo "Setup complete."
echo ""
echo "Activate the virtual environment with:"
echo "  source .venv/bin/activate"
echo ""
echo "Then start the server with:"
echo "  python server.py"
echo ""
echo "And connect as a player with:"
echo "  python client.py"
