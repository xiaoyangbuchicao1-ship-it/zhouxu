#!/usr/bin/env bash
set -Eeuo pipefail

START_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$START_ROOT/../.." && pwd)"
COMPOSE_FILE="$PROJECT_ROOT/deploy/app/compose.yaml"
COMPOSE_ENV="$PROJECT_ROOT/deploy/app/.env"

command -v docker >/dev/null 2>&1 || { echo 'docker is required' >&2; exit 1; }
[[ -f "$COMPOSE_FILE" ]] || { echo "missing Compose file: $COMPOSE_FILE" >&2; exit 1; }
[[ -f "$COMPOSE_ENV" ]] || { echo "missing env file: $COMPOSE_ENV" >&2; exit 1; }

exec docker compose --env-file "$COMPOSE_ENV" -f "$COMPOSE_FILE" down
