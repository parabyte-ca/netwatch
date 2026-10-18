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
# data/ is writable by the container, so this root script never follows anything in it on the
# host: file operations there run in a throwaway container (where a planted symlink can only
# point inside that container), and nothing read from it is trusted unvalidated.
#
# STATE_FILE describes the last *successful* update, so --rollback (and the UI's Roll back
# button) can undo it. A new attempt writes PENDING_FILE and only promotes it to STATE_FILE on
# success, so a failed attempt never destroys the record of the last good update. Both live in
# .state/, which is not mounted into the container.
STATE_DIR="$REPO_DIR/.state"
STATE_FILE="$STATE_DIR/last-update.state"
PENDING_FILE="$STATE_DIR/pending-update.state"
LEGACY_STATE_FILE="$BACKUP_DIR/last-update.state"  # before v0.7.1
KEEP_BACKUPS=10
BACKUP_RE='^pre-update-[0-9]{8}-[0-9]{6}-v[0-9]+\.[0-9]+\.[0-9]+\.db$'

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
[[ -L "$STATE_DIR" ]] && die "$STATE_DIR is a symlink; refusing to continue."
mkdir -p "$STATE_DIR"
chown root:root "$STATE_DIR" 2>/dev/null || true
chmod 700 "$STATE_DIR"

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
        # shellcheck disable=SC2016  # expanded by the container's sh
        in_data_container '
            mkdir -p /data/backups
            cp /data/netwatch.db "/data/backups/$1"
            if [ -f /data/netwatch.db-wal ]; then cp /data/netwatch.db-wal "/data/backups/$1-wal"; fi
        ' "$dest_name"
    fi
    echo "$BACKUP_DIR/$dest_name"
}

# Run a small sh script against data/ inside a throwaway, network-less container.
in_data_container() {
    local script="$1"; shift
    docker run --rm --network none --cap-drop ALL --security-opt no-new-privileges \
        --entrypoint sh -v "$DATA_DIR:/data" "$IMAGE:latest" -c "$script" sh "$@"
}

prune_backups() {
    # Names are matched strictly (no newlines or odd characters can reach rm), find never
    # descends into a symlinked backups/, and rm removes a symlink rather than its target.
    local -a old=()
    local f
    while IFS= read -r -d '' f; do old+=("$f"); done < <(
        find "$BACKUP_DIR" -maxdepth 1 -type f -regextype posix-extended \
            -regex '.*/pre-update-[0-9]{8}-[0-9]{6}-v[0-9]+\.[0-9]+\.[0-9]+\.db' \
            -printf '%T@ %f\0' 2>/dev/null | sort -z -rn | tail -z -n +$((KEEP_BACKUPS + 1)) \
            | cut -z -d' ' -f2-
    )
    for f in "${old[@]}"; do
        [[ "$f" =~ $BACKUP_RE ]] && rm -f "$BACKUP_DIR/$f" "$BACKUP_DIR/$f-wal"
    done
    return 0
}

restore_db() {
    local name="$1"
    [[ -n "$name" ]] || { warn "No backup to restore."; return 0; }
    info "Restoring database from $name ..."
    # shellcheck disable=SC2016  # expanded by the container's sh
    in_data_container '
        set -e
        [ -f "/data/backups/$1" ] || { echo "backup $1 is missing" >&2; exit 1; }
        cp "/data/backups/$1" /data/.restore.tmp
        rm -f /data/netwatch.db-wal /data/netwatch.db-shm
        mv -f /data/.restore.tmp /data/netwatch.db
        if [ -f "/data/backups/$1-wal" ]; then cp "/data/backups/$1-wal" /data/netwatch.db-wal; fi
    ' "$name" || warn "Database restore failed; the current database was kept."
}

# Read and validate a state file. Sets PREV_REF, PREV_REV, PREV_IMAGE and BACKUP (a bare file
# name in backups/). Anything malformed is refused rather than used.
load_state() {
    local file="$1" ref rev image backup
    [[ -f "$file" && ! -L "$file" ]] || die "No usable update record ($file)."
    ref="$(sed -n 's/^PREV_REF=//p' "$file" | head -n 1)"
    rev="$(sed -n 's/^PREV_REV=//p' "$file" | head -n 1)"
    image="$(sed -n 's/^PREV_IMAGE=//p' "$file" | head -n 1)"
    backup="$(sed -n 's/^BACKUP=//p' "$file" | head -n 1)"
    backup="${backup##*/}"
    [[ -z "$ref" || ( "$ref" =~ ^[A-Za-z0-9][A-Za-z0-9._/-]{0,99}$ && "$ref" != *..* ) ]] \
        || die "Invalid PREV_REF in $file."
    [[ -z "$rev" || "$rev" =~ ^[0-9a-f]{40}$ ]] || die "Invalid PREV_REV in $file."
    [[ -z "$image" || "$image" =~ ^ghcr\.io/parabyte-ca/netwatch:rollback-[0-9]+\.[0-9]+\.[0-9]+$ ]] \
        || die "Invalid PREV_IMAGE in $file."
    [[ -z "$backup" || "$backup" =~ $BACKUP_RE ]] || die "Invalid BACKUP in $file."
    PREV_REF="$ref" PREV_REV="$rev" PREV_IMAGE="$image" BACKUP="$backup"
}

restore_code() {
    local ref="$1" rev="$2"
    [[ "$OPT_NO_GIT" == true || -z "$rev" ]] && return 0
    info "Restoring code to ${ref:-detached} @ ${rev:0:12} ..."
    if [[ -n "$ref" ]]; then
        git_repo checkout -q "$ref" --
        git_repo reset -q --hard "$rev" --
    else
        git_repo checkout -q "$rev" --
    fi
}

rollback() {
    local ref="$1" rev="$2" image="$3" backup="$4" consumed="$5"
    warn "Rolling back ..."
    compose logs --no-color --tail 200 > "$STATE_DIR/rollback-$(date +%Y%m%d-%H%M%S).log" 2>&1 || true
    # Nothing below may leave NetWatch stopped: every step is best effort, then it starts again.
    compose stop || true
    restore_db "$backup"
    restore_code "$ref" "$rev" || warn "Could not restore the code to ${rev:0:12}."
    if [[ -n "$image" ]] && docker image inspect "$image" >/dev/null 2>&1; then
        docker tag "$image" "$IMAGE:latest" || warn "Could not re-tag $image."
    else
        warn "Previous image ${image:-(none recorded)} not available; rebuilding from the restored code."
        build_image || warn "Rebuild failed; starting whatever image is tagged :latest."
    fi
    compose up -d --no-build --pull never --remove-orphans || true
    if wait_healthy; then
        rm -f "$consumed"  # this record has been undone
        info "Rollback complete: running $(cat VERSION) again."
        return 0
    fi
    compose logs --tail 50 || true
    die "ROLLBACK FAILED. Manual attention needed (backup: ${backup:-none})."
}

# ── Manual rollback ────────────────────────────────────────────────────────────

if [[ "$OPT_ROLLBACK" == true ]]; then
    record="$STATE_FILE"
    [[ -f "$record" ]] || record="$LEGACY_STATE_FILE"
    load_state "$record"
    # A rollback restores the pre-update database: keep what was collected since, just in case.
    SAFETY="$(backup_db "pre-update-$(date +%Y%m%d-%H%M%S)-v$(cat VERSION).db")"
    [[ -n "$SAFETY" ]] && info "Current database saved to $SAFETY before rolling back."
    rollback "$PREV_REF" "$PREV_REV" "$PREV_IMAGE" "$BACKUP" "$record"
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
    # Per-version tag: a later failed attempt must not overwrite what a rollback restores.
    docker tag "$CURRENT_IMAGE" "$IMAGE:rollback-$PREV_VERSION"
    PREV_IMAGE="$IMAGE:rollback-$PREV_VERSION"
fi

info "Current: v$PREV_VERSION (${PREV_REF:-detached} @ ${PREV_REV:0:12})"

# ── Back up the database ───────────────────────────────────────────────────────

BACKUP_PATH="$(backup_db "pre-update-$(date +%Y%m%d-%H%M%S)-v$PREV_VERSION.db")"
[[ -n "$BACKUP_PATH" ]] && info "Database backed up to $BACKUP_PATH"
BACKUP="${BACKUP_PATH##*/}"
prune_backups

cat > "$PENDING_FILE" <<STATE
PREV_REF=$PREV_REF
PREV_REV=$PREV_REV
PREV_IMAGE=$PREV_IMAGE
BACKUP=$BACKUP
STATE

# ── Release image first: signed by this repo's CI, and built from the tagged commit ─────
#
# For a release, the image is pulled and its signature verified before any code is checked
# out, and the tag must point at the commit the image was built from. So the scripts that run
# as root next are exactly the ones CI built and signed (see README "Signed releases").

VERIFIED_REV=""
if [[ -n "$OPT_VERSION" && "$OPT_BUILD" == false ]]; then
    pull_image "$OPT_VERSION" || die "Could not pull $IMAGE:$OPT_VERSION; nothing was changed."
    if verify_enabled; then
        VERIFIED_REV="$(verify_image "$IMAGE:$OPT_VERSION")" \
            || die "Signature check failed for $IMAGE:$OPT_VERSION; nothing was changed."
    fi
fi

# ── Fetch the new code ─────────────────────────────────────────────────────────

if [[ "$OPT_NO_GIT" == false ]]; then
    if [[ -n "$OPT_VERSION" ]]; then
        info "Checking out release v$OPT_VERSION ..."
        git_repo fetch -q --tags origin
        tag_rev="$(git_repo rev-parse -q --verify "refs/tags/v$OPT_VERSION^{commit}")" \
            || die "Tag v$OPT_VERSION not found; nothing was changed."
        if [[ -n "$VERIFIED_REV" && "$tag_rev" != "$VERIFIED_REV" ]]; then
            die "Tag v$OPT_VERSION ($tag_rev) is not the commit the signed image was built from ($VERIFIED_REV); nothing was changed."
        fi
        git_repo checkout -q "$tag_rev" --
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
    build_image || rollback "$PREV_REF" "$PREV_REV" "$PREV_IMAGE" "$BACKUP" "$PENDING_FILE"
elif [[ -n "$OPT_VERSION" ]]; then
    docker tag "$IMAGE:$OPT_VERSION" "$IMAGE:latest"  # pulled (and verified) above
elif ! pull_image latest; then
    warn "Pull failed; building locally instead."
    build_image || rollback "$PREV_REF" "$PREV_REV" "$PREV_IMAGE" "$BACKUP" "$PENDING_FILE"
elif verify_enabled && ! verify_image "$IMAGE:latest" >/dev/null; then
    # Docker has already moved :latest to the unverified pull; put the running image back.
    warn "Signature check failed for $IMAGE:latest."
    rollback "$PREV_REF" "$PREV_REV" "$PREV_IMAGE" "$BACKUP" "$PENDING_FILE"
    exit 1
fi

# ── Deploy and verify ──────────────────────────────────────────────────────────

for d in upgrade upgrade-status; do
    [[ -L "$REPO_DIR/$d" ]] || mkdir -p "$REPO_DIR/$d"
done
info "Restarting container ..."
compose up -d --no-build --pull never --remove-orphans || warn "docker compose up failed."

if wait_healthy; then
    mv -f "$PENDING_FILE" "$STATE_FILE"
    in_data_container 'rm -f /data/backups/last-update.state /data/backups/pending-update.state' \
        || true  # records from before v0.7.1
    # Only the newest rollback image is still referenced (by STATE_FILE); drop older ones.
    docker images --format '{{.Repository}}:{{.Tag}}' "$IMAGE" \
        | grep -F "$IMAGE:rollback-" | grep -vxF "$PREV_IMAGE" \
        | xargs -r docker rmi >/dev/null 2>&1 || true
    info "Update complete: v$PREV_VERSION -> v$NEW_VERSION. Rollback with: sudo ./update.sh --rollback"
    # Remove superseded, untagged NetWatch images older than a week.
    docker image prune -f --filter "label=org.opencontainers.image.source=https://github.com/parabyte-ca/netwatch" \
        --filter "until=168h" >/dev/null 2>&1 || true
    exit 0
fi

rollback "$PREV_REF" "$PREV_REV" "$PREV_IMAGE" "$BACKUP" "$PENDING_FILE"
exit 1
