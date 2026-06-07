#!/usr/bin/env bash
# Run the test suite for end_point_blank (Python / pytest).
set -euo pipefail
cd "$(dirname "$0")"
exec python -m pytest "$@"
