#!/usr/bin/env bash
# start_provider.sh — Start the EDC provider connector (foreground)
set -euo pipefail

# ── Configuration ─────────────────────────────────────────────────────────────
EDC_SAMPLES_DIR="${EDC_SAMPLES_DIR:-/app/edc-samples}"
PROVIDER_JAR="$EDC_SAMPLES_DIR/transfer/transfer-03-consumer-pull/provider-proxy-data-plane/build/libs/connector.jar"
PROVIDER_CONFIG="${PROVIDER_CONFIG:-$EDC_SAMPLES_DIR/transfer/transfer-03-consumer-pull/resources/configuration/provider.properties}"

# ── Helpers ───────────────────────────────────────────────────────────────────
log() { echo "[provider] $*"; }
die() { echo "[provider] ERROR: $*" >&2; exit 1; }

# ── Validate JAR exists ───────────────────────────────────────────────────────
[ -f "$PROVIDER_JAR" ] || die "Provider JAR not found: $PROVIDER_JAR"
[ -f "$PROVIDER_CONFIG" ] || die "Provider config not found: $PROVIDER_CONFIG"

# ── Start Provider connector (foreground) ─────────────────────────────────────
log "Starting provider connector..."
log "JAR: $PROVIDER_JAR"
log "Config: $PROVIDER_CONFIG"

exec java -Dedc.fs.config="$PROVIDER_CONFIG" -jar "$PROVIDER_JAR"
