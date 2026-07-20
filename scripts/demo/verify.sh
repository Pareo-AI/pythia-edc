#!/usr/bin/env bash
# verify.sh — One-command regression gate for the Pythia demo (via `./demo verify`).
#
# Runs the offline unit suite, brings up the local demo stack, runs the live
# smoke beats against it, and always tears the stack down again. Exits non-zero
# if anything fails, so a single run tells you whether the demo still works.
#
# Usage:
#   ./demo verify            # full gate
#   ./demo verify -k beat1   # extra args are forwarded to the smoke pytest
#
# Prerequisites: the EDC sample JARs must be built (see `./demo up`). LM Studio is
# optional — the synthesis beat skips (does not fail) if it is not running.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"

EXTRAS=(--extra dev --extra ask --extra trust --extra mcp)
LMSTUDIO_URL="${LMSTUDIO_URL:-http://localhost:1234/v1}"

log()  { echo "[verify] $*"; }
fail() { echo "[verify] FAIL: $*" >&2; exit 1; }

stack_up=0
cleanup() {
    if [ "$stack_up" = "1" ]; then
        log "Tearing down demo stack ..."
        bash "$SCRIPT_DIR/down.sh" >/dev/null 2>&1 || true
    fi
}
trap cleanup EXIT

# ── Stage 1: offline unit suite ───────────────────────────────────────────────
log "Stage 1/3 — unit suite (live tests excluded) ..."
uv run "${EXTRAS[@]}" python -m pytest -q \
    --ignore=tests/test_integration.py \
    --ignore=tests/test_demo_smoke.py \
    || fail "unit suite failed — fix logic regressions before checking the live demo"
log "Stage 1 passed."

# ── Stage 2: bring up the local demo stack ────────────────────────────────────
log "Stage 2/3 — starting demo stack ..."
if curl -sf "$LMSTUDIO_URL/models" -o /dev/null 2>/dev/null; then
    log "LM Studio reachable — synthesis beat will run."
else
    log "LM Studio NOT reachable at $LMSTUDIO_URL — synthesis beat will SKIP (not fail)."
fi
stack_up=1
bash "$SCRIPT_DIR/up.sh" || fail "demo up failed — stack did not come up"
log "Stage 2 passed — stack is up."

# ── Stage 3: live smoke beats ─────────────────────────────────────────────────
log "Stage 3/3 — live smoke beats ..."
bash "$SCRIPT_DIR/smoke.sh" "$@" || fail "smoke beats failed — the demo is broken"
log "Stage 3 passed."

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "============================================================"
echo " demo verify: ALL STAGES PASSED — demo is good to go."
echo "============================================================"
