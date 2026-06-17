#!/usr/bin/env bash
# HUMAN-ONLY — regenerates Playwright screenshot baselines.
# Usage: bash scripts/update-screenshots.sh

set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../apps/astro" && pwd)"
cd "$APP_DIR"

echo "=== Installing Playwright browsers (if missing) ==="
npx playwright install chromium --with-deps 2>/dev/null || npx playwright install chromium

echo ""
echo "=== Regenerating screenshot baselines ==="
if [ $# -ge 1 ]; then
  npx playwright test \
    --config tests/e2e/playwright.config.ts \
    --update-snapshots "$1" \
    tests/e2e/
else
  npx playwright test \
    --config tests/e2e/playwright.config.ts \
    --update-snapshots \
    tests/e2e/
fi

echo ""
echo "=== Done ==="
echo "Review the updated screenshots in tests/e2e/screenshots/ before committing."
