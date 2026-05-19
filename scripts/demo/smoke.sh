#!/usr/bin/env bash
# smoke.sh — Run Pythia demo smoke beats against the live EDC stack.
#
# Prerequisites: stack must already be running (`./demo up`).
#
# Usage:
#   ./demo smoke   (or: bash scripts/demo/smoke.sh)
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

cd "$PROJECT_ROOT"

EDC_LIVE=1 uv run \
    --extra dev \
    --extra ask \
    --extra trust \
    --extra mcp \
    python -m pytest tests/test_demo_smoke.py -v "$@"
