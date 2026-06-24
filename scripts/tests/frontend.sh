#!/bin/sh
# CI runner for agentic frontend tests (vitest unit + playwright E2E).
# Runs inside the astro test container via docker compose.
output_dir=""
for arg in "$@"; do
  case "$arg" in
    --output-dir=*) output_dir="${arg#*=}" ;;
  esac
done

PROJECT_DIR="/usr/local/Wywy-Website/Wywy-Codes"
CONFIG_DIR="/etc/Wywy-Website-Control/config"

docker compose \
  -f "$PROJECT_DIR/docker/docker-compose.base.yml" \
  -f "$PROJECT_DIR/docker/docker-compose.dev.yml" \
  -f "$PROJECT_DIR/docker/docker-compose.test.yml" \
  --env-file "$CONFIG_DIR/.env" \
  --env-file "$CONFIG_DIR/agentic/.env" \
  --env-file "$CONFIG_DIR/.env.dev" \
  run --rm astro
exit_code=$?

if [ -n "$output_dir" ]; then
  if [ $exit_code -eq 0 ]; then
    echo '{"name":"frontend-tests","status":"passed"}' > "$output_dir/results.jsonl"
  else
    echo '{"name":"frontend-tests","status":"failed"}' > "$output_dir/results.jsonl"
  fi
fi

exit $exit_code
