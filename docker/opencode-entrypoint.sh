#!/bin/bash
# opencode-entrypoint.sh — Wrapper that starts opencode serve and warms up the model.
#
# This script is set as ENTRYPOINT in the agent image.  The runtime command
# (e.g. "opencode serve --port 4096 --hostname 0.0.0.0") is passed as
# arguments by container_manager.py.
#
# Flow:
#   1. Start the real server in the background.
#   2. Wait for the health endpoint to respond (max 60 s).
#   3. Send a warm-up message to trigger model loading (synchronous).
#   4. Wait for the server process (keeps the container alive).

set -o errexit
set -o nounset

# ── 1. Parse port from arguments (default 4096) ──────────────────────
PORT=4096
prev=""
for arg in "$@"; do
    if [ "$prev" = "--port" ]; then
        PORT="$arg"
    fi
    prev="$arg"
done

# ── 2. Start the real server in the background ────────────────────────
"$@" &
SERVER_PID=$!

_cleanup() {
    # Forward SIGTERM/SIGINT to the server process
    if kill -0 "$SERVER_PID" 2>/dev/null; then
        kill "$SERVER_PID" 2>/dev/null
        wait "$SERVER_PID" 2>/dev/null || true
    fi
    exit 0
}
trap _cleanup SIGTERM SIGINT SIGQUIT

# ── 3. Wait for health endpoint (max 60 s, 0.5 s intervals) ──────────
HEALTH_URL="http://localhost:${PORT}/global/health"
healthy=false
for _ in $(seq 1 120); do
    if curl -sf "$HEALTH_URL" > /dev/null 2>&1; then
        healthy=true
        break
    fi
    sleep 0.5
done

if [ "$healthy" = false ]; then
    echo "WARNING: Server did not become healthy within 60 s — skipping warm-up" >&2
    wait "$SERVER_PID"
fi

# ── 4. Warm up: trigger model inference ───────────────────────────────
#    Create a session and send a message synchronously.  This loads the
#    model so the first real request doesn't pay the cold-start penalty.
#    Note: the Python-side ``wait_healthy()`` also does a warm-up of its
#    own, so this is an extra head-start within the container.
#    Controlled by the OPENCODE_WARMUP env var (default: 1 = enabled).
if [ "${OPENCODE_WARMUP:-1}" != "0" ]; then
WARMUP_SESSION=$(curl -sf -X POST "http://localhost:${PORT}/session" \
    -H "Content-Type: application/json" \
    -d '{"title":"warmup"}' 2>/dev/null \
    | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" \
    2>/dev/null || true)

if [ -n "$WARMUP_SESSION" ]; then
    # Synchronous warm-up — block until the model responds.
    # This ensures model loading starts before the caller's first request.
    curl -sf -X POST "http://localhost:${PORT}/session/${WARMUP_SESSION}/message" \
        -H "Content-Type: application/json" \
        -d '{"parts":[{"type":"text","text":"Respond with one word: ready"}]}' \
        -o /dev/null 2>&1 || true
fi
fi

# ── 5. Wait for server to finish (foreground) ────────────────────────
wait "$SERVER_PID"
