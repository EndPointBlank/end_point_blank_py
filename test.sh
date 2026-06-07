#!/usr/bin/env bash
# Run the test suite for end_point_blank (Python / pytest).
# Uses the virtualenv created by build.sh when present.
set -euo pipefail
cd "$(dirname "$0")"
if [ -x .venv/bin/python ]; then
  exec .venv/bin/python -m pytest "$@"
else
  exec python -m pytest "$@"
fi
