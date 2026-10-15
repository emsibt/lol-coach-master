#!/usr/bin/env bash
# langfuse.sh -- Run the local Langfuse stack (AI observability) with Docker.
#
# Langfuse is where the agent's prompts, completions, token usage, and cost are
# recorded. The stack lives in ops/langfuse/docker-compose.yml and provisions a
# fixed dev API key on first boot, matching the LANGFUSE_* block in .env.example.
#
# Requires Docker Desktop (or any Docker Engine with the `docker compose` plugin).
#
# Usage:
#   bash scripts/langfuse.sh up       # start (detached)
#   bash scripts/langfuse.sh status   # container health
#   bash scripts/langfuse.sh logs     # follow logs
#   bash scripts/langfuse.sh down     # stop, keep data
#   bash scripts/langfuse.sh reset    # stop and delete all data (volumes)
set -euo pipefail

cd "$(dirname "$0")/.."

COMPOSE="ops/langfuse/docker-compose.yml"

if ! command -v docker >/dev/null 2>&1; then
    echo "    docker not found. Install Docker Desktop and retry:"
    echo "      https://www.docker.com/products/docker-desktop/"
    exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
    echo "    'docker compose' is not available (the compose plugin is missing)."
    exit 1
fi

case "${1:-up}" in
    up)     docker compose -f "$COMPOSE" up -d ;;
    down)   docker compose -f "$COMPOSE" down ;;
    stop)   docker compose -f "$COMPOSE" stop ;;
    logs)   docker compose -f "$COMPOSE" logs -f --tail=100 ;;
    status) docker compose -f "$COMPOSE" ps ;;
    reset)  docker compose -f "$COMPOSE" down -v ;;
    *)
        echo "usage: $0 {up|down|stop|logs|status|reset}"
        exit 2
        ;;
esac
