#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="/etc/Wywy-Website-Control/config/agentic/.env"

if [ -f "$ENV_FILE" ]; then
    set -a
    source "$ENV_FILE"
    set +a
fi

# Ensure opencode fork is cloned
OPENCODE_REPO_PATH="${OPENCODE_REPO_PATH:-/usr/local/Wywy-Website/opencode}"
OPENCODE_REPO_URL="${OPENCODE_REPO_URL:-https://github.com/WywySenarios/opencode.git}"
OPENCODE_UPSTREAM_URL="${OPENCODE_UPSTREAM_URL:-https://github.com/anomalyco/opencode.git}"

if [ ! -d "$OPENCODE_REPO_PATH" ]; then
    echo "Cloning opencode fork to $OPENCODE_REPO_PATH..."
    git clone "$OPENCODE_REPO_URL" "$OPENCODE_REPO_PATH"
    git -C "$OPENCODE_REPO_PATH" remote add upstream "$OPENCODE_UPSTREAM_URL" 2>/dev/null || true
fi

OPENCODE_PATH="${OPENCODE_HOST_BINARY_PATH:-/home/pc/.opencode/bin/opencode}"
if [ ! -f "$OPENCODE_PATH" ]; then
    echo "ERROR: opencode binary not found at $OPENCODE_PATH"
    echo "Install opencode first: see https://opencode.ai"
    exit 1
fi

# Copy binary into build context
cp "$OPENCODE_PATH" "${REPO_ROOT}/docker/opencode"

# Build image
docker build \
    -f "${REPO_ROOT}/docker/Dockerfile.agent" \
    --build-arg CONTAINER_GID="${CONTAINER_GID:-2523}" \
    -t wywy/agent \
    "${REPO_ROOT}/docker"

# Clean up
rm "${REPO_ROOT}/docker/opencode"

echo "Image built: wywy/agent"
