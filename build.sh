#!/usr/bin/env bash
# Create a virtualenv and install the package + dev deps so the tests can run.
set -euo pipefail
cd "$(dirname "$0")"
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
exec .venv/bin/pip install -e ".[dev]"
