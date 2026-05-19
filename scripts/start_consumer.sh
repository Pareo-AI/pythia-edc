#!/usr/bin/env bash
# start_consumer.sh — Start the EDC consumer connector (foreground)
set -euo pipefail

# ── Configuration ─────────────────────────────────────────────────────────────
EDC_SAMPLES_DIR="${EDC_SAMPLES_DIR:-/app/edc-samples}"
CONSUMER_JAR="$EDC_SAMPLES_DIR/transfer/transfer-00-prerequisites/connector/build/libs/connector.jar"
CONSUMER_CONFIG="${CONSUMER_CONFIG:-$EDC_SAMPLES_DIR/transfer/transfer-00-prerequisites/resources/configuration/consumer-configuration.properties}"

# ── Helpers ───────────────────────────────────────────────────────────────────
log() { echo "[consumer] $*"; }
die() { echo "[consumer] ERROR: $*" >&2; exit 1; }

# ── Validate JAR exists ───────────────────────────────────────────────────────
[ -f "$CONSUMER_JAR" ] || die "Consumer JAR not found: $CONSUMER_JAR"
[ -f "$CONSUMER_CONFIG" ] || die "Consumer config not found: $CONSUMER_CONFIG"

# ── Start Consumer connector (foreground) ─────────────────────────────────────
log "Starting consumer connector..."
log "JAR: $CONSUMER_JAR"
log "Config: $CONSUMER_CONFIG"

exec java -Dedc.fs.config="$CONSUMER_CONFIG" -jar "$CONSUMER_JAR"
