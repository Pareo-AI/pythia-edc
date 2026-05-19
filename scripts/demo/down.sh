#!/usr/bin/env bash
# down.sh — Stop the Pythia demo environment (invoked via `./demo down`)
# Usage: ./demo down   (or: bash scripts/demo/down.sh)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PIDS_FILE="/tmp/pythia-demo.pids"

# Provider ports come from topology.py (one connector per logical provider);
# fall back to the known 3-provider block if topology can't be read. Always
# include the consumer block (29xxx).
CONSUMER_PORTS="29191 29192 29193 29194 29291"
_PROVIDER_PORTS="$(python3 "$SCRIPT_DIR/lib/topology.py" --print-ports 2>/dev/null || true)"
if [ -z "$_PROVIDER_PORTS" ]; then
    _PROVIDER_PORTS="19191 19192 19193 19194 19291 39191 39192 39193 39194 39291 49191 49192 49193 49194 49291"
fi
CONNECTOR_PORTS="${CONNECTOR_PORTS:-$_PROVIDER_PORTS $CONSUMER_PORTS}"
MOCK_PORT="${MOCK_PORT:-9876}"

log() { echo "[down] $*"; }

if [ ! -f "$PIDS_FILE" ]; then
    log "No PID file found at $PIDS_FILE — nothing to stop"
    exit 0
fi

PIDS=()
IFS=$'\n' read -r -d '' -a PIDS < "$PIDS_FILE" || true

STOPPED=0
for pid in "${PIDS[@]:-}"; do
    if [ -z "$pid" ]; then
        continue
    fi
    if kill -0 "$pid" 2>/dev/null; then
        log "Stopping PID $pid ..."
        kill -TERM "$pid" 2>/dev/null || true
        sleep 0.5
        # Force-kill if still running
        if kill -0 "$pid" 2>/dev/null; then
            kill -KILL "$pid" 2>/dev/null || true
            log "  Force-killed PID $pid"
        else
            log "  Stopped PID $pid"
        fi
        STOPPED=$((STOPPED + 1))
    else
        log "PID $pid is not running (already stopped)"
    fi
done

rm -f "$PIDS_FILE"
log "Removed $PIDS_FILE"

# Also kill any stragglers on known ports
for port in $CONNECTOR_PORTS "$MOCK_PORT"; do
    pids=$(lsof -ti tcp:"$port" 2>/dev/null || true)
    if [ -n "$pids" ]; then
        log "Killing straggler on port $port (PID: $pids)"
        echo "$pids" | xargs kill -TERM 2>/dev/null || true
        sleep 0.5
        # Escalate to KILL for anything that ignored TERM (e.g. the JVM connectors)
        pids=$(lsof -ti tcp:"$port" 2>/dev/null || true)
        if [ -n "$pids" ]; then
            echo "$pids" | xargs kill -KILL 2>/dev/null || true
        fi
    fi
done

echo ""
echo "[down] Demo environment stopped ($STOPPED process(es) terminated)."
