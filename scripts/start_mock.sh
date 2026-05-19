#!/usr/bin/env bash
# start_mock.sh — Start the mock data server (foreground)
set -euo pipefail

# ── Configuration ─────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MOCK_PORT="${MOCK_PORT:-9876}"

# ── Helpers ───────────────────────────────────────────────────────────────────
log() { echo "[mock-server] $*"; }

# ── Start mock server (foreground) ────────────────────────────────────────────
log "Starting mock data server on port $MOCK_PORT..."

exec python3 "$SCRIPT_DIR/demo/lib/mock_server.py"
