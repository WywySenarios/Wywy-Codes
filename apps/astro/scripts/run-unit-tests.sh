#!/usr/bin/env bash
# Run agentic frontend unit tests via vitest.
# Usage: cd apps/astro && bash scripts/run-unit-tests.sh

set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_DIR"

echo "=== Installing dependencies (if needed) ==="
npm install --silent

echo ""
echo "=== Running unit tests ==="
npx vitest run --config vitest.config.ts "$@"
