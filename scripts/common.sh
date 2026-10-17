#!/usr/bin/env bash
# Shared helpers for setup.sh and update.sh. Sourced, not executed.
# shellcheck disable=SC2034  # variables are used by the sourcing scripts

PROJECT="netwatch"
CONTAINER_NAME="netwatch"
IMAGE="ghcr.io/parabyte-ca/netwatch"
HEALTH_TIMEOUT_S="${HEALTH_TIMEOUT_S:-180}"

info() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) ==> $*"; }
warn() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) WARN: $*" >&2; }
die()  { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) ERROR: $*" >&2; exit 1; }

require_docker() {
    command -v docker >/dev/null 2>&1 || die "docker not found."
    docker info >/dev/null 2>&1 || die "cannot talk to Docker. Run with sudo."
    docker compose version >/dev/null 2>&1 || die "docker compose plugin not found."
}

# Read KEY from .env without executing it (falls back to $2).
env_value() {
    local key="$1" default="${2:-}" line
    if [[ -f "$REPO_DIR/.env" ]]; then
        line="$(grep -E "^[[:space:]]*${key}=" "$REPO_DIR/.env" | tail -n 1 || true)"
        if [[ -n "$line" ]]; then
            line="${line#*=}"
            line="${line%%#*}"
            line="${line//\"/}"
            line="${line//\'/}"
            echo "${line//[[:space:]]/}"
            return
        fi
    fi
    echo "$default"
}

# Run git as the owner of the checkout so a sudo run never leaves root-owned files behind
# (and avoids git's "dubious ownership" refusal).
git_repo() {
    local owner
    owner="$(stat -c %U "$REPO_DIR")"
    if [[ $EUID -eq 0 && "$owner" != "root" ]]; then
        runuser -u "$owner" -- git -C "$REPO_DIR" "$@"
    else
        git -C "$REPO_DIR" "$@"
    fi
}

compose() {
    APP_VERSION="$(cat "$REPO_DIR/VERSION")" docker compose --project-directory "$REPO_DIR" \
        -f "$REPO_DIR/docker-compose.yml" "$@"
}

# Pull IMAGE:<tag> and retag it as :latest (what docker-compose.yml runs). Returns 1 on failure.
pull_image() {
    local tag="$1"
    info "Pulling $IMAGE:$tag ..."
    docker pull "$IMAGE:$tag" || return 1
    [[ "$tag" == "latest" ]] || docker tag "$IMAGE:$tag" "$IMAGE:latest"
}

build_image() {
    local version
    version="$(cat "$REPO_DIR/VERSION")"
    info "Building $IMAGE:$version locally ..."
    docker build --build-arg "APP_VERSION=$version" \
        -t "$IMAGE:$version" -t "$IMAGE:latest" "$REPO_DIR"
}

wait_healthy() {
    local waited=0 state
    info "Waiting for health check (up to ${HEALTH_TIMEOUT_S}s) ..."
    while (( waited < HEALTH_TIMEOUT_S )); do
        state="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' \
            "$CONTAINER_NAME" 2>/dev/null || echo missing)"
        case "$state" in
            healthy) info "Container is healthy."; return 0 ;;
            unhealthy|exited|dead|missing) warn "Container state: $state"; return 1 ;;
        esac
        sleep 2
        waited=$((waited + 2))
    done
    warn "Timed out waiting for a healthy container."
    return 1
}

lan_url() {
    local port bind ip
    port="$(env_value NETWATCH_PORT 8085)"
    bind="$(env_value NETWATCH_BIND 0.0.0.0)"
    if [[ "$bind" == "0.0.0.0" || -z "$bind" ]]; then
        ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
    else
        ip="$bind"
    fi
    echo "http://${ip:-localhost}:${port}"
}
