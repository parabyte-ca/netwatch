#!/usr/bin/env bash
# update.sh: update NetWatch in place, with a database backup, a health check, and an
# automatic rollback (previous code, previous image, pre-update database) if the new
# version does not come up healthy.
#
# Usage:  sudo ./update.sh [options]
#   --version X.Y.Z   Deploy release vX.Y.Z (pulls ghcr.io/parabyte-ca/netwatch:X.Y.Z).
#                     Without it, tracks the main branch and the :latest image.
#   --build           Build the image locally instead of pulling it.
#   --no-git          Skip the git step (deploy the code already checked out).
#   --rollback        Undo the last update: restore its code, image and database backup.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/common.sh
source "$REPO_DIR/scripts/common.sh"

BRANCH="main"
DATA_DIR="$REPO_DIR/data"
BACKUP_DIR="$DATA_DIR/backups"
STATE_FILE="$BACKUP_DIR/last-update.state"
KEEP_BACKUPS=10

OPT_VERSION=""
OPT_BUILD=false
OPT_NO_GIT=false
OPT_ROLLBACK=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --version)  OPT_VERSION="${2:-}"; shift 2 ;;
        --build)    OPT_BUILD=true; shift ;;
        --no-git)   OPT_NO_GIT=true; shift ;;
        --rollback) OPT_ROLLBACK=true; shift ;;
        -h|--help)  sed -n '2,13p' "$0"; exit 0 ;;
        *) die "Unknown option: $1" ;;
    esac
done

if [[ -n "$OPT_VERSION" ]]; then
    OPT_VERSION="${OPT_VERSION#v}"
    [[ "$OPT_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "--version must be X.Y.Z"
fi

require_docker
cd "$REPO_DIR"

# ── Helpers ────────────────────────────────────────────────────────────────────

backup_db() {
    local dest_name="$1"
    mkdir -p "$BACKUP_DIR"
    if [[ ! -f "$DATA_DIR/netwatch.db" ]]; then
        warn "No database yet; skipping backup."
        echo ""
        return
    fi
    if docker exec "$CONTAINER_NAME" true 2>/dev/null; then
        # Online, WAL-consistent snapshot via SQLite's backup API inside the container.
        docker exec "$CONTAINER_NAME" python -m netwatch.backup "/data/backups/$dest_name" >/dev/null
    else
        # Container is not running, so the files are quiescent: a plain copy is consistent.
        cp -p "$DATA_DIR/netwatch.db" "$BACKUP_DIR/$dest_name"
        [[ -f "$DATA_DIR/netwatch.db-wal" ]] && cp -p "$DATA_DIR/netwatch.db-wal" "$BACKUP_DIR/$dest_name-wal"
    fi
    echo "$BACKUP_DIR/$dest_name"
}

prune_backups() {
    local old
    old="$(find "$BACKUP_DIR" -maxdepth 1 -name 'pre-update-*.db' -printf '%T@ %p\n' 2>/dev/null \
        | sort -rn | tail -n +$((KEEP_BACKUPS + 1)) | cut -d' ' -f2-)"
    [[ -z "$old" ]] && return 0
    while IFS= read -r f; do rm -f "$f" "$f-wal"; done <<< "$old"
}

restore_db() {
    local backup="$1"
    [[ -n "$backup" && -f "$backup" ]] || { warn "No backup to restore."; return 0; }
    info "Restoring database from $(basename "$backup") ..."
    rm -f "$DATA_DIR/netwatch.db-wal" "$DATA_DIR/netwatch.db-shm"
    cp -p "$backup" "$DATA_DIR/netwatch.db"
    [[ -f "$backup-wal" ]] && cp -p "$backup-wal" "$DATA_DIR/netwatch.db-wal"
    return 0
}

restore_code() {
    local ref="$1" rev="$2"
    [[ "$OPT_NO_GIT" == true || -z "$rev" ]] && return 0
    info "Restoring code to ${ref:-detached} @ ${rev:0:12} ..."
    if [[ -n "$ref" ]]; then
        git_repo checkout -q "$ref"
        git_repo reset -q --hard "$rev"
    else
        git_repo checkout -q "$rev"
    fi
}

rollback() {
    local ref="$1" rev="$2" image="$3" backup="$4"
    warn "Rolling back ..."
    compose logs --no-color --tail 200 > "$BACKUP_DIR/failed-update-$(date +%Y%m%d-%H%M%S).log" 2>&1 || true
    compose stop || true
    restore_db "$backup"
    restore_code "$ref" "$rev"
    if [[ -n "$image" ]]; then
        docker tag "$image" "$IMAGE:latest"
    else
        warn "No previous image recorded; rebuilding from the restored code."
        build_image
    fi
    compose up -d --no-build --pull never --remove-orphans || true
    if wait_healthy; then
        info "Rollback complete: running $(cat VERSION) again."
        return 0
    fi
    compose logs --tail 50 || true
    die "ROLLBACK FAILED. Manual attention needed (backup: ${backup:-none})."
}

# ── Manual rollback ────────────────────────────────────────────────────────────

if [[ "$OPT_ROLLBACK" == true ]]; then
    [[ -f "$STATE_FILE" ]] || die "No previous update recorded ($STATE_FILE)."
    PREV_REF="$(sed -n 's/^PREV_REF=//p' "$STATE_FILE")"
    PREV_REV="$(sed -n 's/^PREV_REV=//p' "$STATE_FILE")"
    PREV_IMAGE="$(sed -n 's/^PREV_IMAGE=//p' "$STATE_FILE")"
    BACKUP="$(sed -n 's/^BACKUP=//p' "$STATE_FILE")"
    rollback "$PREV_REF" "$PREV_REV" "$PREV_IMAGE" "$BACKUP"
    rm -f "$STATE_FILE"
    exit 0
fi

# ── Pre-flight ─────────────────────────────────────────────────────────────────

if [[ "$OPT_NO_GIT" == false ]] && [[ -n "$(git_repo status --porcelain --untracked-files=no)" ]]; then
    die "Tracked files have local changes; commit or discard them first (git status)."
fi

PREV_REV="$(git_repo rev-parse HEAD)"
PREV_REF="$(git_repo symbolic-ref -q --short HEAD || true)"
PREV_VERSION="$(cat VERSION)"
# Keep the running image under a stable tag so a rollback never depends on a dangling image.
PREV_IMAGE=""
if CURRENT_IMAGE="$(docker inspect -f '{{.Image}}' "$CONTAINER_NAME" 2>/dev/null)"; then
    docker tag "$CURRENT_IMAGE" "$IMAGE:previous"
    PREV_IMAGE="$IMAGE:previous"
fi

info "Current: v$PREV_VERSION (${PREV_REF:-detached} @ ${PREV_REV:0:12})"

# ── Back up the database ───────────────────────────────────────────────────────

BACKUP="$(backup_db "pre-update-$(date +%Y%m%d-%H%M%S)-v$PREV_VERSION.db")"
[[ -n "$BACKUP" ]] && info "Database backed up to $BACKUP"
prune_backups

cat > "$STATE_FILE" <<STATE
PREV_REF=$PREV_REF
PREV_REV=$PREV_REV
PREV_IMAGE=$PREV_IMAGE
BACKUP=$BACKUP
STATE

# ── Fetch the new code ─────────────────────────────────────────────────────────

if [[ "$OPT_NO_GIT" == false ]]; then
    if [[ -n "$OPT_VERSION" ]]; then
        info "Checking out release v$OPT_VERSION ..."
        git_repo fetch -q --tags origin
        git_repo checkout -q "v$OPT_VERSION" || die "Tag v$OPT_VERSION not found."
    else
        [[ "$PREV_REF" == "$BRANCH" ]] || git_repo checkout -q "$BRANCH"
        info "Pulling $BRANCH ..."
        git_repo pull -q --ff-only origin "$BRANCH"
    fi
fi

NEW_VERSION="$(cat VERSION)"
info "Deploying v$NEW_VERSION"

# ── Get the image ──────────────────────────────────────────────────────────────

if [[ "$OPT_BUILD" == true ]]; then
    build_image || rollback "$PREV_REF" "$PREV_REV" "$PREV_IMAGE" "$BACKUP"
elif ! pull_image "${OPT_VERSION:-latest}"; then
    warn "Pull failed; building locally instead."
    build_image || rollback "$PREV_REF" "$PREV_REV" "$PREV_IMAGE" "$BACKUP"
fi

# ── Deploy and verify ──────────────────────────────────────────────────────────

info "Restarting container ..."
compose up -d --no-build --pull never --remove-orphans || warn "docker compose up failed."

if wait_healthy; then
    info "Update complete: v$PREV_VERSION -> v$NEW_VERSION. Rollback with: sudo ./update.sh --rollback"
    # Remove superseded, untagged NetWatch images older than a week.
    docker image prune -f --filter "label=org.opencontainers.image.source=https://github.com/parabyte-ca/netwatch" \
        --filter "until=168h" >/dev/null 2>&1 || true
    exit 0
fi

rollback "$PREV_REF" "$PREV_REV" "$PREV_IMAGE" "$BACKUP"
exit 1
