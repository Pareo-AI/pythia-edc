#!/usr/bin/env bash
# up.sh — Start the full Pythia demo environment (invoked via `./demo up`)
# Usage: ./demo up   (or: bash scripts/demo/up.sh)
#
# By default this launches ONE local EDC provider connector per logical provider
# defined in scripts/demo/lib/datasets.py (currently 3: rheinmobil, zugspitze,
# donautech), plus a consumer connector and a local mock data server, and seeds
# each provider with only its own datasets. The provider topology (ids + ports)
# comes from scripts/demo/lib/topology.py — the single source of truth.
#
# It can also point at EXTERNAL connectors (optionally over TLS) via env vars; set
# START_LOCAL_CONNECTORS=0 to connect to remote/already-running connectors instead
# of launching local Java JARs. Set CONSUMER_ONLY=1 to start ONLY a local consumer
# pointed at remote providers (no local provider, mock, or seeding). Set DRY_RUN=1
# to print the resolved toggle plan and exit 0 without launching anything.
set -euo pipefail

# ── Absolute paths ────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
# Path to a local checkout of the Eclipse EDC Samples repo
# (https://github.com/eclipse-edc/Samples). Override via the EDC_SAMPLES_DIR env var.
EDC_SAMPLES_DIR="${EDC_SAMPLES_DIR:-$PROJECT_ROOT/.edc-samples}"

PROVIDER_JAR="$EDC_SAMPLES_DIR/transfer/transfer-03-consumer-pull/provider-proxy-data-plane/build/libs/connector.jar"
CONSUMER_JAR="$EDC_SAMPLES_DIR/transfer/transfer-00-prerequisites/connector/build/libs/connector.jar"
CONSUMER_CONFIG="$EDC_SAMPLES_DIR/transfer/transfer-00-prerequisites/resources/configuration/consumer-configuration.properties"

# Generated per-provider connector configs land here (one .properties per provider).
GEN_CONFIG_DIR="${PYTHIA_GEN_CONFIG_DIR:-/tmp/pythia-demo-config}"

# ── Tunables (env-overridable; defaults reproduce today's local behavior) ──────
START_LOCAL_CONNECTORS="${START_LOCAL_CONNECTORS:-1}"
CONSUMER_ONLY="${CONSUMER_ONLY:-0}"

# Granular per-connector toggles. Default to the umbrella so existing behavior is unchanged.
START_PROVIDER="${START_PROVIDER:-$START_LOCAL_CONNECTORS}"
START_CONSUMER="${START_CONSUMER:-$START_LOCAL_CONNECTORS}"

START_MOCK_SERVER="${START_MOCK_SERVER:-1}"
SEED_PROVIDER="${SEED_PROVIDER:-1}"

# Readiness waits. Default 1 preserves the previous unconditional waits.
WAIT_FOR_PROVIDER="${WAIT_FOR_PROVIDER:-1}"
WAIT_FOR_CONSUMER="${WAIT_FOR_CONSUMER:-1}"

DRY_RUN="${DRY_RUN:-0}"

PROVIDER_MGMT="${PROVIDER_MGMT:-http://localhost:19193/management}"
CONSUMER_MGMT="${CONSUMER_MGMT:-http://localhost:29193/management}"

# ── CONSUMER_ONLY profile (wins over granular vars) ───────────────────────────
if [ "$CONSUMER_ONLY" = "1" ]; then
    START_PROVIDER=0
    START_CONSUMER=1
    START_MOCK_SERVER=0
    SEED_PROVIDER=0
    WAIT_FOR_PROVIDER=0
fi

# ── DRY_RUN plan mode — print resolved toggles and exit before any side effects ─
if [ "$DRY_RUN" = "1" ]; then
    echo "[plan] START_PROVIDER=$START_PROVIDER"
    echo "[plan] START_CONSUMER=$START_CONSUMER"
    echo "[plan] START_MOCK_SERVER=$START_MOCK_SERVER"
    echo "[plan] SEED_PROVIDER=$SEED_PROVIDER"
    echo "[plan] WAIT_FOR_PROVIDER=$WAIT_FOR_PROVIDER"
    echo "[plan] WAIT_FOR_CONSUMER=$WAIT_FOR_CONSUMER"
    echo "[plan] PROVIDER_MGMT=$PROVIDER_MGMT"
    echo "[plan] CONSUMER_MGMT=$CONSUMER_MGMT"
    exit 0
fi

EDC_API_KEY="${EDC_API_KEY:-password}"
EDC_API_KEY_HEADER="${EDC_API_KEY_HEADER:-X-Api-Key}"

MOCK_PORT="${MOCK_PORT:-9876}"
MOCK_BIND="${MOCK_BIND:-0.0.0.0}"

CURL_INSECURE="${CURL_INSECURE:-0}"

CONSUMER_LOG="/tmp/edc-consumer.log"
MOCK_LOG="/tmp/mock-server.log"
PIDS_FILE="/tmp/pythia-demo.pids"

# ── Helpers ───────────────────────────────────────────────────────────────────
log()  { echo "[up] $*"; }
die()  { echo "[up] ERROR: $*" >&2; exit 1; }

kill_port() {
    local port="$1"
    local pids
    pids=$(lsof -ti tcp:"$port" 2>/dev/null || true)
    if [ -n "$pids" ]; then
        log "Killing existing process(es) on port $port (PID: $pids)"
        echo "$pids" | xargs kill -TERM 2>/dev/null || true
        sleep 0.5
        echo "$pids" | xargs kill -KILL 2>/dev/null || true
    fi
}

wait_for_url() {
    local url="$1"
    local label="$2"
    local max_wait=60
    local elapsed=0
    local insecure=""
    if [ "$CURL_INSECURE" = "1" ]; then
        insecure="-k"
    fi
    log "Waiting for $label ($url) ..."
    # Any HTTP response (even 4xx) means the connector is alive; exit code != 0 means not reachable
    while true; do
        local http_code
        if http_code=$(curl -s $insecure -o /dev/null -w "%{http_code}" \
            -H "$EDC_API_KEY_HEADER: $EDC_API_KEY" \
            -H "Content-Type: application/json" \
            -X POST "$url/v3/assets/request" \
            --data '{"@context":{"@vocab":"https://w3id.org/edc/v0.0.1/ns/"}}' \
            --connect-timeout 2 \
            2>/dev/null) && [ -n "$http_code" ] && [ "$http_code" -ge 100 ] 2>/dev/null; then
            echo ""
            log "$label is ready (${elapsed}s, HTTP $http_code)"
            return 0
        fi
        if [ "$elapsed" -ge "$max_wait" ]; then
            die "$label did not become ready within ${max_wait}s. Check /tmp/edc-provider-*.log / $CONSUMER_LOG"
        fi
        sleep 1
        elapsed=$((elapsed + 1))
        printf "."
    done
}

# Write a self-contained consumer-pull provider config for one logical provider.
# Args: id api_port control_port mgmt_port protocol_port public_port
gen_provider_config() {
    local pid="$1" api="$2" ctrl="$3" mgmt="$4" proto="$5" public="$6"
    local out="$GEN_CONFIG_DIR/provider-$pid.properties"
    cat > "$out" <<EOF
edc.participant.id=$pid
edc.dsp.callback.address=http://localhost:$proto/protocol
web.http.port=$api
web.http.path=/api
web.http.management.port=$mgmt
web.http.management.path=/management
web.http.protocol.port=$proto
web.http.protocol.path=/protocol
edc.transfer.proxy.token.signer.privatekey.alias=private-key
edc.transfer.proxy.token.verifier.publickey.alias=public-key
web.http.public.port=$public
web.http.public.path=/public
web.http.control.port=$ctrl
web.http.control.path=/control
edc.dataplane.proxy.public.endpoint=http://localhost:$public/public
EOF
    echo "$out"
}

# ── Resolve the local provider topology (ids + ports) ─────────────────────────
# Parallel arrays, one entry per local provider connector to launch.
PROVIDER_IDS=()
P_API=(); P_CTRL=(); P_MGMT_PORT=(); P_PROTO=(); P_PUBLIC=(); P_MGMT_URL=()
PROVIDER_PORTS=""
if [ "$START_PROVIDER" = "1" ]; then
    while IFS='|' read -r pid api ctrl mgmt proto public mgmturl; do
        [ -z "$pid" ] && continue
        PROVIDER_IDS+=("$pid")
        P_API+=("$api"); P_CTRL+=("$ctrl"); P_MGMT_PORT+=("$mgmt")
        P_PROTO+=("$proto"); P_PUBLIC+=("$public"); P_MGMT_URL+=("$mgmturl")
        PROVIDER_PORTS="$PROVIDER_PORTS $api $ctrl $mgmt $proto $public"
    done < <(python3 "$SCRIPT_DIR/lib/topology.py" --print-launch)
    [ "${#PROVIDER_IDS[@]}" -gt 0 ] || die "topology.py returned no providers"
fi

# Ports to clear before launch (providers we resolved + consumer block).
CONSUMER_PORTS="29191 29192 29193 29194 29291"
CONNECTOR_PORTS="${CONNECTOR_PORTS:-$PROVIDER_PORTS $CONSUMER_PORTS}"

# ── Validate JARs exist (only for the connectors we launch locally) ───────────
if [ "$START_PROVIDER" = "1" ]; then
    [ -f "$PROVIDER_JAR" ]    || die "Provider JAR not found: $PROVIDER_JAR\n  Run: cd $EDC_SAMPLES_DIR && ./gradlew :transfer:transfer-03-consumer-pull:provider-proxy-data-plane:build"
fi
if [ "$START_CONSUMER" = "1" ]; then
    [ -f "$CONSUMER_JAR" ]    || die "Consumer JAR not found: $CONSUMER_JAR\n  Run: cd $EDC_SAMPLES_DIR && ./gradlew :transfer:transfer-00-prerequisites:connector:build"
    [ -f "$CONSUMER_CONFIG" ] || die "Consumer config not found: $CONSUMER_CONFIG"
fi

# ── Kill any existing processes on EDC/mock ports ────────────────────────────
log "Clearing ports ..."
if [ "$START_PROVIDER" = "1" ] || [ "$START_CONSUMER" = "1" ]; then
    for port in $CONNECTOR_PORTS; do
        kill_port "$port"
    done
fi
if [ "$START_MOCK_SERVER" = "1" ]; then
    kill_port "$MOCK_PORT"
fi
sleep 1

# PIDs of processes we actually start (only these get written to PIDS_FILE)
STARTED_PIDS=""

# ── Start provider connectors (one per logical provider) ──────────────────────
if [ "$START_PROVIDER" = "1" ]; then
    mkdir -p "$GEN_CONFIG_DIR"
    for i in "${!PROVIDER_IDS[@]}"; do
        pid="${PROVIDER_IDS[$i]}"
        cfg="$(gen_provider_config "$pid" "${P_API[$i]}" "${P_CTRL[$i]}" \
                                   "${P_MGMT_PORT[$i]}" "${P_PROTO[$i]}" "${P_PUBLIC[$i]}")"
        plog="/tmp/edc-provider-$pid.log"
        log "Starting provider '$pid' (mgmt ${P_MGMT_PORT[$i]}, dsp ${P_PROTO[$i]}) → $plog"
        java -Dedc.fs.config="$cfg" -jar "$PROVIDER_JAR" > "$plog" 2>&1 &
        ppid=$!
        log "  Provider '$pid' PID: $ppid"
        STARTED_PIDS="$STARTED_PIDS $ppid"
    done
fi

if [ "$START_CONSUMER" = "1" ]; then
    log "Starting consumer connector → $CONSUMER_LOG"
    java \
        -Dedc.fs.config="$CONSUMER_CONFIG" \
        -jar "$CONSUMER_JAR" \
        > "$CONSUMER_LOG" 2>&1 &
    CONSUMER_PID=$!
    log "Consumer PID: $CONSUMER_PID"
    STARTED_PIDS="$STARTED_PIDS $CONSUMER_PID"
fi

if [ "$START_PROVIDER" != "1" ] && [ "$START_CONSUMER" != "1" ]; then
    log "No local connectors started — using remote/already-running connectors"
fi

# ── Wait for management APIs (local or remote) ────────────────────────────────
if [ "$WAIT_FOR_PROVIDER" = "1" ]; then
    if [ "$START_PROVIDER" = "1" ]; then
        for i in "${!PROVIDER_IDS[@]}"; do
            wait_for_url "${P_MGMT_URL[$i]}" "Provider '${PROVIDER_IDS[$i]}' management API"
        done
    else
        # Remote / single-provider mode: wait on the configured PROVIDER_MGMT.
        wait_for_url "$PROVIDER_MGMT" "Provider management API"
    fi
fi
if [ "$WAIT_FOR_CONSUMER" = "1" ]; then
    wait_for_url "$CONSUMER_MGMT" "Consumer management API"
fi

# ── Start mock data server ────────────────────────────────────────────────────
if [ "$START_MOCK_SERVER" = "1" ]; then
    log "Starting mock data server on $MOCK_BIND:$MOCK_PORT → $MOCK_LOG"
    cd "$PROJECT_ROOT"
    MOCK_PORT="$MOCK_PORT" MOCK_BIND="$MOCK_BIND" python3 "$SCRIPT_DIR/lib/mock_server.py" > "$MOCK_LOG" 2>&1 &
    MOCK_PID=$!
    log "Mock server PID: $MOCK_PID"
    STARTED_PIDS="$STARTED_PIDS $MOCK_PID"

    # Wait briefly for mock server to bind
    sleep 1
    if ! kill -0 "$MOCK_PID" 2>/dev/null; then
        die "Mock server failed to start. Check $MOCK_LOG"
    fi
    # Verify mock server responds
    if ! curl -sf "http://localhost:$MOCK_PORT/" -o /dev/null 2>/dev/null; then
        log "Mock server started (index endpoint may return 200 or other; continuing)"
    fi
else
    log "START_MOCK_SERVER=0 — skipping local mock data server"
fi

# ── Save PIDs (only processes we actually started) ────────────────────────────
if [ -n "$STARTED_PIDS" ]; then
    printf '%s\n' $STARTED_PIDS > "$PIDS_FILE"
    log "PIDs saved to $PIDS_FILE"
else
    : > "$PIDS_FILE"
    log "No local processes started; wrote empty $PIDS_FILE"
fi

# ── Seed demo data ────────────────────────────────────────────────────────────
if [ "$SEED_PROVIDER" = "1" ]; then
    log "Seeding provider(s) with demo assets ..."
    cd "$PROJECT_ROOT"
    export PYTHIA_API_KEY="$EDC_API_KEY"
    export PYTHIA_API_KEY_HEADER="$EDC_API_KEY_HEADER"
    # PYTHIA_MOCK_BASE_URL (if set in the environment) is honored so the seed and
    # lib/datasets agree on the data-source baseUrl the provider fetches from.
    if [ -n "${PYTHIA_MOCK_BASE_URL:-}" ]; then
        export PYTHIA_MOCK_BASE_URL
    fi
    if [ "$START_PROVIDER" = "1" ]; then
        # Multi-provider: seed each connector with only its own datasets.
        PYTHIA_SEED_TARGETS="$(python3 "$SCRIPT_DIR/lib/topology.py" --print-seed-targets)"
        export PYTHIA_SEED_TARGETS
    else
        # Remote / single-provider: seed everything to the one PROVIDER_MGMT.
        export PYTHIA_PROVIDER_MGMT_URL="$PROVIDER_MGMT"
    fi
    uv run python "$SCRIPT_DIR/lib/seed.py"
else
    log "SEED_PROVIDER=0 — skipping provider seeding (assumed already seeded)"
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo "============================================================"
echo " Demo ready."
if [ "$START_PROVIDER" = "1" ]; then
    echo " Providers (${#PROVIDER_IDS[@]} local connectors):"
    for i in "${!PROVIDER_IDS[@]}"; do
        echo "   - ${PROVIDER_IDS[$i]}: ${P_MGMT_URL[$i]} (dsp :${P_PROTO[$i]})"
    done
else
    echo " Provider:   $PROVIDER_MGMT (remote / not launched)"
fi
if [ "$START_CONSUMER" = "1" ]; then consumer_status="local JAR launched"; else consumer_status="remote (not launched)"; fi
if [ "$START_MOCK_SERVER" = "1" ]; then mock_status="started on $MOCK_BIND:$MOCK_PORT"; else mock_status="skipped"; fi
if [ "$SEED_PROVIDER" = "1" ]; then seed_status="seeded"; else seed_status="skipped"; fi
echo " Consumer:   $CONSUMER_MGMT ($consumer_status)"
echo " Mock data:  $mock_status"
echo " Seeding:    $seed_status"
echo ""
echo " Run integration tests:"
echo "   EDC_LIVE=1 uv run python -m pytest tests/test_integration.py -v"
echo ""
echo " To stop: ./demo down"
echo "============================================================"
