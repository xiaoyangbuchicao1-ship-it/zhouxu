#!/usr/bin/env bash
set -Eeuo pipefail

# Linux is the production/container entry point. It deliberately does not
# create a native .venv or install Node packages on the host.
START_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$START_ROOT/../.." && pwd)"
COMPOSE_FILE="$PROJECT_ROOT/deploy/app/compose.yaml"
COMPOSE_ENV="$PROJECT_ROOT/deploy/app/.env"

die() { printf 'start failed: %s\n' "$1" >&2; exit 1; }
command -v docker >/dev/null 2>&1 || die 'docker is required'
docker compose version >/dev/null 2>&1 || die 'docker compose plugin is required'
[[ -f "$COMPOSE_FILE" ]] || die "missing Compose file: $COMPOSE_FILE"
[[ -f "$COMPOSE_ENV" ]] || die "missing $COMPOSE_ENV; copy .env.example and set secrets"

exec docker compose --env-file "$COMPOSE_ENV" -f "$COMPOSE_FILE" up -d --build "$@"
