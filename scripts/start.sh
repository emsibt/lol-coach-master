#!/usr/bin/env bash
# start.sh -- Bring the whole local stack up in Docker: Langfuse + the app.
#
# The app is docker-compose.yml (backend :8000, frontend :8080). Langfuse is
# ops/langfuse/docker-compose.yml (UI :3000) and must start first, because the
# backend reaches it at http://langfuse-web:3000 over the langfuse_default
# network. This script starts Langfuse, starts the app, then joins the backend
# to that network (compose alone does not attach it).
#
# Works both inside a Docker Engine WSL distro and from Windows Git Bash, where
# it forwards every command to `wsl.exe -d DockerEngine`. It also starts a
# keepalive, since that distro shuts down ~60s after its last process and takes
# the containers' published ports with it.
#
# Usage:
#   bash scripts/start.sh              # start Langfuse + app (no rebuild)
#   bash scripts/start.sh --build      # rebuild the app images, then start
#   bash scripts/start.sh restart      # restart both stacks
#   bash scripts/start.sh stop         # stop both, keep all data
#   bash scripts/start.sh status       # container health for both stacks
#   bash scripts/start.sh logs [svc]   # follow logs (all, or one service)
#
# Env:
#   WSL_DISTRO   Docker Engine distro to forward to (default: DockerEngine)
set -euo pipefail

cd "$(dirname "$0")/.."

APP_COMPOSE="docker-compose.yml"
LANGFUSE_COMPOSE="ops/langfuse/docker-compose.yml"
WSL_DISTRO="${WSL_DISTRO:-DockerEngine}"

die() { echo "error: $*" >&2; exit 1; }

# --- locate a working Docker CLI --------------------------------------------
MODE=""
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    MODE="native"
elif command -v wsl.exe >/dev/null 2>&1; then
    MODE="wsl"
else
    die "docker not found (and no wsl.exe to forward to). Start Docker or set WSL_DISTRO."
fi

if [ "$MODE" = "wsl" ]; then
    wsl.exe -l -q 2>/dev/null | tr -d '\r\0' | grep -qx "$WSL_DISTRO" \
        || die "WSL distro '$WSL_DISTRO' not found (wsl -l -v)"
    WIN_DIR="$(pwd -W 2>/dev/null || pwd)"
    LINUX_DIR="$(wsl.exe -d "$WSL_DISTRO" -- wslpath -a "$WIN_DIR" | tr -d '\r')"
    [ -n "$LINUX_DIR" ] || die "could not map $WIN_DIR into $WSL_DISTRO"
    # --cd is not supported by every WSL build, so cd inside the shell instead.
    run() { wsl.exe -d "$WSL_DISTRO" -- bash -lc "cd '$LINUX_DIR' && $*"; }
else
    run() { bash -lc "$*"; }
fi

ensure_keepalive() {
    [ "$MODE" = "wsl" ] || return 0
    wsl.exe -d "$WSL_DISTRO" -u root -- pgrep -f 'sleep infinity' >/dev/null 2>&1 && return 0
    powershell.exe -NoProfile -Command \
        "Start-Process wsl.exe -ArgumentList '-d','$WSL_DISTRO','-u','root','sleep','infinity' -WindowStyle Hidden" \
        >/dev/null 2>&1 || true
}

ensure_docker() {
    ensure_keepalive
    run "docker info >/dev/null 2>&1" && return 0
    echo "==> docker daemon not responding; trying to start it"
    if [ "$MODE" = "wsl" ]; then
        wsl.exe -d "$WSL_DISTRO" -u root -- service docker start >/dev/null 2>&1 || true
    fi
    for _ in $(seq 1 30); do
        run "docker info >/dev/null 2>&1" && return 0
        sleep 1
    done
    die "docker daemon did not come up"
}

# Compose does not know about the network Langfuse creates, so join it here.
# Idempotent: a re-connect fails harmlessly.
connect_langfuse_network() {
    run "docker network inspect langfuse_default >/dev/null 2>&1" || return 0
    local cid
    cid="$(run "docker compose -f '$APP_COMPOSE' ps -q backend" | tr -d '\r')"
    [ -n "$cid" ] || return 0
    run "docker network connect langfuse_default '$cid' >/dev/null 2>&1 || true"
}

compose_up() {
    local build="${1:-}"
    echo "==> Langfuse"
    run "docker compose -f '$LANGFUSE_COMPOSE' up -d"
    echo "==> app${build:+ (rebuilding images)}"
    run "docker compose -f '$APP_COMPOSE' up -d $build"
    connect_langfuse_network
}

case "${1:-up}" in
    up|start)
        [ -f .env ] || echo "warning: no .env at the repo root; the backend will run without keys" >&2
        ensure_docker
        compose_up "${2:-}"
        echo
        echo "app       http://localhost:8080   (api :8000)"
        echo "langfuse  http://localhost:3000   (dev@lolcoach.local / lolcoach)"
        ;;
    restart)
        ensure_docker
        run "docker compose -f '$LANGFUSE_COMPOSE' restart"
        run "docker compose -f '$APP_COMPOSE' restart"
        connect_langfuse_network
        ;;
    stop|down)
        ensure_docker
        run "docker compose -f '$APP_COMPOSE' down"
        run "docker compose -f '$LANGFUSE_COMPOSE' down"
        ;;
    status)
        ensure_docker
        echo "==> app"
        run "docker compose -f '$APP_COMPOSE' ps"
        echo "==> Langfuse"
        run "docker compose -f '$LANGFUSE_COMPOSE' ps"
        ;;
    logs)
        ensure_docker
        case "${2:-}" in
            langfuse) run "docker compose -f '$LANGFUSE_COMPOSE' logs -f --tail=100" ;;
            app|"")   run "docker compose -f '$APP_COMPOSE' logs -f --tail=100" ;;
            *)        run "docker compose -f '$APP_COMPOSE' logs -f --tail=100 '$2'" ;;
        esac
        ;;
    *)
        echo "usage: $0 [up|restart|stop|status|logs] [--build|service]" >&2
        exit 2
        ;;
esac
