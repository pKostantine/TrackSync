#!/usr/bin/env bash
# Run TrackSync from source, without building an app.
set -uo pipefail
cd "$(dirname "$0")"
unset PYTHONHOME PYTHONPATH
export PYTHONNOUSERSITE=1

if [ ! -x .venv/bin/python ]; then
    echo "First run: creating a virtual environment and installing dependencies..."
    PY=""
    for c in python3.13 python3.12 python3.11 python3; do
        command -v "$c" >/dev/null 2>&1 && { PY="$c"; break; }
    done
    [ -n "$PY" ] || { echo "No Python 3 found."; exit 1; }
    "$PY" -m venv .venv || exit 1
    .venv/bin/python -m pip install --upgrade pip --quiet
    .venv/bin/python -m pip install -r requirements.txt || exit 1
fi
exec .venv/bin/python TrackSync.py "$@"
