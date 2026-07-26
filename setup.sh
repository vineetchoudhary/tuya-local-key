#!/usr/bin/env bash
# Create a virtualenv and install dependencies for tuya-local-key.
set -euo pipefail
cd "$(dirname "$0")"

PY="${PYTHON:-python3}"
VENV=".venv"

echo "==> Creating virtualenv in $VENV"
"$PY" -m venv "$VENV"

echo "==> Installing dependencies"
"$VENV/bin/python" -m pip install --quiet --upgrade pip setuptools wheel
"$VENV/bin/python" -m pip install --quiet -r requirements.txt

echo
echo "Done. Run it with:"
echo "    $VENV/bin/python tuya_devices.py"
echo
echo "Or install the 'tuya-local-key' command into the venv:"
echo "    $VENV/bin/python -m pip install -e ."
echo "    $VENV/bin/tuya-local-key"
