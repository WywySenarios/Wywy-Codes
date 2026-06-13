#!/usr/bin/env bash
# Agentic frontend test runner — executed inside the astro Docker container.
# Invoked by docker-compose.test.yml via /etc/Wywy-Website-Control/run.sh agentic test.

set -euo pipefail

cd /app

echo "=== Installing dependencies ==="
npm install

echo ""
echo "=== Playwright browsers (with system deps) ==="
npx playwright install --with-deps chromium 2>/dev/null || npx playwright install chromium

echo ""
echo "=== Frontend unit tests (vitest) ==="
set +e
npx vitest run --config vitest.config.ts
UNIT_EXIT=$?
set -e

echo ""
echo "=== Frontend E2E tests (playwright) ==="
set +e
npx playwright test --config tests/e2e/playwright.config.ts
E2E_EXIT=$?
set -e

if [ $UNIT_EXIT -ne 0 ] || [ $E2E_EXIT -ne 0 ]; then
  echo ""
  echo "FAILURE: unit=$UNIT_EXIT e2e=$E2E_EXIT"
  exit 1
fi
echo ""
echo "All frontend tests passed."
