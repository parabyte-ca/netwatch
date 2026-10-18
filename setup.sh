#!/usr/bin/env bash
# setup.sh: first-run deployment of NetWatch. Idempotent; safe to re-run.
#
# Usage:  sudo ./setup.sh [--build] [--no-agent]
#   --build      Build the image locally instead of pulling it from GHCR.
#   --no-agent   Don't install the upgrade agent (disables in-app updates).
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/common.sh
source "$REPO_DIR/scripts/common.sh"

OPT_BUILD=false
OPT_AGENT=true
while [[ $# -gt 0 ]]; do
    case "$1" in
        --build) OPT_BUILD=true; shift ;;
        --no-agent) OPT_AGENT=false; shift ;;
        -h|--help) sed -n '2,7p' "$0"; exit 0 ;;
        *) die "Unknown option: $1" ;;
    esac
done

VERSION="$(cat "$REPO_DIR/VERSION" 2>/dev/null || echo latest)"
info "Setting up $PROJECT v$VERSION in $REPO_DIR"
require_docker

# ── Configuration ──────────────────────────────────────────────────────────────
if [[ ! -f "$REPO_DIR/.env" ]]; then
    cp "$REPO_DIR/.env.example" "$REPO_DIR/.env"
    chmod 600 "$REPO_DIR/.env"
    chown --reference="$REPO_DIR" "$REPO_DIR/.env" 2>/dev/null || true  # editable without sudo
    info "Created .env from .env.example (review it, then re-run if you change anything)."
fi
PORT="$(env_value NETWATCH_PORT 8090)"

# ── Persistent data (bind mount) ───────────────────────────────────────────────
# The container runs as root with every capability except NET_RAW/NET_ADMIN dropped, so it
# can only write where root is the owner. Keep the data directory root-owned.
mkdir -p "$REPO_DIR/data/backups"
chown root:root "$REPO_DIR/data" "$REPO_DIR/data/backups" 2>/dev/null \
    || warn "could not chown data/ to root; the container may be unable to write to it."
chmod 750 "$REPO_DIR/data" 2>/dev/null || true
# Upgrade agent folders: upgrade/ receives requests from the container, upgrade-status/ is the
# agent's reply (read-only in the container), .state/ is the agent's own (not mounted).
mkdir -p "$REPO_DIR/upgrade" "$REPO_DIR/upgrade-status" "$REPO_DIR/.state"
chown root:root "$REPO_DIR/upgrade" "$REPO_DIR/upgrade-status" "$REPO_DIR/.state" 2>/dev/null || true
chmod 700 "$REPO_DIR/upgrade" "$REPO_DIR/.state" 2>/dev/null || true

# ── Port check ─────────────────────────────────────────────────────────────────
if command -v ss >/dev/null 2>&1; then
    listener="$(ss -Htlnp "sport = :$PORT" 2>/dev/null || true)"
    if [[ -n "$listener" ]] && ! docker ps --format '{{.Names}}' | grep -qx "$CONTAINER_NAME"; then
        die "port $PORT is already in use by another process:
$listener
Pick a free port (see the dev-sop port registry) and set NETWATCH_PORT in .env."
    fi
fi

# ── Image ──────────────────────────────────────────────────────────────────────
if [[ "$OPT_BUILD" == true ]]; then
    build_image
elif pull_image "$VERSION"; then
    if verify_enabled && ! verify_image "$IMAGE:$VERSION" >/dev/null; then
        warn "Could not verify the signature of $IMAGE:$VERSION (releases before v0.7.1 are unsigned)."
    fi
    docker tag "$IMAGE:$VERSION" "$IMAGE:latest"
else
    warn "Could not pull $IMAGE:$VERSION; building it locally from this checkout."
    build_image
fi

# ── Run ────────────────────────────────────────────────────────────────────────
info "Starting container ..."
compose up -d --no-build --pull never --remove-orphans

if wait_healthy; then
    if [[ "$OPT_AGENT" == true ]]; then
        bash "$REPO_DIR/scripts/upgrade-agent.sh" --install \
            || warn "upgrade agent not installed; in-app updates will be unavailable."
    fi
    info "$PROJECT v$VERSION is running at $(lan_url)"
    info "First visit: create the admin account in the browser."
else
    compose logs --tail 50 || true
    die "$PROJECT did not become healthy. See the logs above."
fi
