#!/usr/bin/env bash
# upgrade-agent.sh: host-side helper that performs in-app update requests for NetWatch.
#
# The container cannot (and should not) update itself. When you press "Update" in the web UI,
# NetWatch writes upgrade/request.json. This agent, run every minute by cron as root, picks the
# request up and runs update.sh, which backs up the database, health-checks the new version and
# rolls back automatically on failure. Progress is reported back through upgrade-status/.
#
# Trust boundary: the container can write upgrade/ (and data/), so this root process treats
# everything there as hostile: it only ever reads request.json (no symlinks, size-capped) and
# deletes it. Its own files live where the container cannot reach: upgrade-status/ (mounted
# read-only into the container) and .state/ (not mounted at all).
#
# Usage:
#   sudo scripts/upgrade-agent.sh --install     register the every-minute cron job
#   sudo scripts/upgrade-agent.sh --uninstall   remove it
#   sudo scripts/upgrade-agent.sh --status      show cron job, heartbeat and last result
#   sudo scripts/upgrade-agent.sh               one pass (what cron runs)
#
# On TrueNAS SCALE the cron job is created through the middleware (midclt), so it appears under
# System > Advanced > Cron Jobs and survives OS upgrades (unlike files in /etc/cron.d).
set -euo pipefail

AGENT_VERSION="2"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INBOX="$REPO_DIR/upgrade"          # container-writable: request.json only
OUT_DIR="$REPO_DIR/upgrade-status" # agent-owned, read-only in the container
STATE_DIR="$REPO_DIR/.state"       # agent-owned, not mounted
SCRIPT_PATH="$REPO_DIR/scripts/upgrade-agent.sh"
CRON_DESC="NetWatch upgrade agent"
CRON_FILE="/etc/cron.d/netwatch-upgrade-agent"
AGENT_LOG="$STATE_DIR/agent.log"

ts()   { date -u +%Y-%m-%dT%H:%M:%SZ; }
info() { echo "$(ts) $*"; }
die()  { echo "$(ts) ERROR: $*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "run as root (sudo)."
command -v python3 >/dev/null 2>&1 || die "python3 is required."

cron_command() {
    printf '/bin/bash %q >> %q 2>&1' "$SCRIPT_PATH" "$AGENT_LOG"
}

# ── install / uninstall / status ──────────────────────────────────────────────

midclt_job_id() {
    midclt call cronjob.query "[[\"description\",\"=\",\"$CRON_DESC\"]]" \
        | python3 -c 'import json,sys; jobs=json.load(sys.stdin); print(jobs[0]["id"] if jobs else "")'
}

# Our own directories must be real directories owned by root (never symlinks).
prepare_dirs() {
    local d
    for d in "$INBOX" "$OUT_DIR" "$STATE_DIR"; do
        [[ -L "$d" ]] && die "$d is a symlink; refusing to continue."
        mkdir -p "$d"
        chown root:root "$d"
    done
    chmod 700 "$INBOX" "$STATE_DIR"
    chmod 755 "$OUT_DIR"
}

installed_cron_command() {
    if command -v midclt >/dev/null 2>&1; then
        midclt call cronjob.query "[[\"description\",\"=\",\"$CRON_DESC\"]]" \
            | python3 -c 'import json,sys; j=json.load(sys.stdin); print(j[0]["command"] if j else "")'
    elif [[ -f "$CRON_FILE" ]]; then
        sed -n 's/^\* \* \* \* \* root //p' "$CRON_FILE"
    fi
}

install_cron() {
    prepare_dirs
    chmod 755 "$SCRIPT_PATH"
    if command -v midclt >/dev/null 2>&1; then
        local payload id
        payload="$(CMD="$(cron_command)" DESC="$CRON_DESC" python3 -c '
import json, os
print(json.dumps({
    "user": "root",
    "command": os.environ["CMD"],
    "description": os.environ["DESC"],
    "schedule": {"minute": "*", "hour": "*", "dom": "*", "month": "*", "dow": "*"},
    "enabled": True,
    "stdout": True,
    "stderr": True,
}))')"
        id="$(midclt_job_id)"
        if [[ -n "$id" ]]; then
            midclt call cronjob.update "$id" "$payload" >/dev/null
            info "Updated TrueNAS cron job #$id ($CRON_DESC)."
        else
            midclt call cronjob.create "$payload" >/dev/null
            info "Created TrueNAS cron job ($CRON_DESC), visible under System > Advanced > Cron Jobs."
        fi
    else
        printf '* * * * * root %s\n' "$(cron_command)" > "$CRON_FILE"
        chmod 644 "$CRON_FILE"
        info "Installed $CRON_FILE (runs every minute)."
    fi
}

uninstall_cron() {
    if command -v midclt >/dev/null 2>&1; then
        local id
        id="$(midclt_job_id)"
        if [[ -n "$id" ]]; then
            midclt call cronjob.delete "$id" >/dev/null
            info "Removed TrueNAS cron job #$id."
        else
            info "No TrueNAS cron job named '$CRON_DESC'."
        fi
    fi
    if [[ -f "$CRON_FILE" ]]; then
        rm -f "$CRON_FILE"
        info "Removed $CRON_FILE."
    fi
}

show_status() {
    if command -v midclt >/dev/null 2>&1; then
        echo "TrueNAS cron job: $( [[ -n "$(midclt_job_id)" ]] && echo installed || echo 'not installed')"
    else
        echo "cron file: $( [[ -f "$CRON_FILE" ]] && echo "$CRON_FILE" || echo 'not installed')"
    fi
    for f in "$OUT_DIR/agent.json" "$OUT_DIR/status.json" "$INBOX/request.json"; do
        [[ -f "$f" && ! -L "$f" ]] && echo "$(basename "$f"): $(head -c 4096 "$f")"
    done
    return 0
}

case "${1:-}" in
    --install)   install_cron; exit 0 ;;
    --uninstall) uninstall_cron; exit 0 ;;
    --status)    show_status; exit 0 ;;
    "")          ;;
    *)           die "unknown option: $1 (use --install, --uninstall or --status)" ;;
esac

# ── one agent pass ────────────────────────────────────────────────────────────

prepare_dirs

# Keep our own log small.
if [[ -f "$AGENT_LOG" ]] && (( $(stat -c %s "$AGENT_LOG") > 1048576 )); then
    mv -f "$AGENT_LOG" "$AGENT_LOG.1"
fi

exec 9>"$STATE_DIR/agent.lock"
flock -n 9 || exit 0  # a previous pass is still running an update

# Agents before v2 logged into (and were run by a cron line writing to) the container-writable
# folder: move the cron line to the new log and drop our old files there (rm never follows a
# symlink). The history is carried over once, as validated JSON lines.
if [[ "$(installed_cron_command)" != "$(cron_command)" ]]; then
    install_cron && info "Upgraded the cron job to agent v$AGENT_VERSION."
fi
if [[ ! -e "$OUT_DIR/history.jsonl" ]]; then
    python3 - "$INBOX/history.jsonl" "$OUT_DIR/history.jsonl" <<'PY' || true
import json, os, stat, sys
src, dest = sys.argv[1], sys.argv[2]
try:
    fd = os.open(src, os.O_RDONLY | os.O_NOFOLLOW)
except OSError:
    sys.exit(0)
with os.fdopen(fd, "rb") as f:
    if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
        sys.exit(0)
    raw = f.read(256 * 1024)
lines = []
for line in raw.decode("utf-8", "replace").splitlines()[-100:]:
    try:
        item = json.loads(line)
    except ValueError:
        continue
    if isinstance(item, dict):
        lines.append(json.dumps({str(k)[:40]: v for k, v in item.items() if isinstance(v, (str, int, type(None)))}))
with open(dest, "w") as f:
    f.write("".join(l + "\n" for l in lines))
PY
fi
for f in agent.json status.json upgrade.log upgrade.log.1 history.jsonl agent.log agent.log.1 .lock; do
    rm -f "${INBOX:?}/$f"
done

# Atomically write a JSON file from KEY=VALUE pairs (values are strings; "null"/ints converted).
write_json() {
    local dest="$1"; shift
    python3 - "$dest" "$@" <<'PY'
import json, os, sys, tempfile
dest, pairs = sys.argv[1], sys.argv[2:]
data = {}
for pair in pairs:
    key, _, value = pair.partition("=")
    if value == "null":
        data[key] = None
    elif value.lstrip("-").isdigit():
        data[key] = int(value)
    else:
        data[key] = value
for key in ("can_rollback",):
    if key in data:
        data[key] = data[key] == "yes"
fd, tmp = tempfile.mkstemp(dir=os.path.dirname(dest), prefix=".tmp.")
with os.fdopen(fd, "w") as f:
    json.dump(data, f)
os.chmod(tmp, 0o644)
os.replace(tmp, dest)
PY
}

CAN_ROLLBACK="no"
[[ -f "$STATE_DIR/last-update.state" || -f "$REPO_DIR/data/backups/last-update.state" ]] && CAN_ROLLBACK="yes"
write_json "$OUT_DIR/agent.json" "last_seen=$(ts)" "every_minutes=1" \
    "agent_version=$AGENT_VERSION" "can_rollback=$CAN_ROLLBACK"

# A "running" status while we hold the lock means a previous pass died mid-update.
if [[ -f "$OUT_DIR/status.json" ]] && grep -q '"state": "running"' "$OUT_DIR/status.json"; then
    python3 - "$OUT_DIR/status.json" "$(ts)" <<'PY'
import json, sys
path, now = sys.argv[1], sys.argv[2]
st = json.load(open(path))
st.update(state="failed", finished_at=now, error="agent interrupted during the update")
json.dump(st, open(path, "w"))
PY
fi

REQUEST="$INBOX/request.json"
[[ -e "$REQUEST" || -L "$REQUEST" ]] || exit 0

# Validate the request strictly; nothing from it reaches a shell unchecked. It is opened
# without following symlinks, must be a small regular file, and is deleted either way.
if ! PARSED="$(python3 - "$REQUEST" <<'PY'
import json, os, re, stat, sys
try:
    fd = os.open(sys.argv[1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as f:
        assert stat.S_ISREG(os.fstat(f.fileno()).st_mode)
        raw = f.read(4097)
    assert len(raw) <= 4096
    r = json.loads(raw)
    assert isinstance(r, dict)
    rid, action, version = str(r.get("id", "")), r.get("action"), r.get("version")
    assert re.fullmatch(r"[0-9a-f]{32}", rid)
    assert action in ("update", "rollback")
    if action == "update":
        assert isinstance(version, str) and re.fullmatch(r"\d+\.\d+\.\d+", version)
    else:
        version = ""
    who = re.sub(r"[^A-Za-z0-9_.@-]", "", str(r.get("requested_by", "")))[:64]
    reason = "auto" if r.get("reason") == "auto" else "manual"
except Exception:
    sys.exit(1)
print(rid, action, version or "-", who or "-", reason)
PY
)"; then
    info "Invalid request.json; discarding it."
    rm -rf --one-file-system "$REQUEST"
    exit 0
fi
read -r REQ_ID ACTION VERSION REQ_BY REASON <<< "$PARSED"
[[ "$VERSION" == "-" ]] && VERSION=""
rm -rf --one-file-system "$REQUEST"

FROM_VERSION="$(cat "$REPO_DIR/VERSION" 2>/dev/null || echo unknown)"
# The app only offers newer releases, but don't take its word for it: no downgrades here
# (going back is what --rollback, with its database backup, is for).
if [[ "$ACTION" == "update" ]] && ! python3 - "$FROM_VERSION" "$VERSION" <<'PY'
import sys
cur, new = (tuple(int(x) for x in v.split(".")) for v in sys.argv[1:3])
sys.exit(0 if new > cur else 1)
PY
then
    info "Refusing update request $REQ_ID: v$VERSION is not newer than v$FROM_VERSION."
    exit 0
fi
STARTED="$(ts)"
info "Starting $ACTION ${VERSION:+to v$VERSION }(request $REQ_ID by $REQ_BY, $REASON)"
write_json "$OUT_DIR/status.json" "id=$REQ_ID" "action=$ACTION" "version=${VERSION:-null}" \
    "state=running" "started_at=$STARTED" "from_version=$FROM_VERSION" \
    "requested_by=$REQ_BY" "reason=$REASON"

LOG="$OUT_DIR/upgrade.log"
[[ -f "$LOG" ]] && mv -f "$LOG" "$LOG.1"
set +e
if [[ "$ACTION" == "update" ]]; then
    bash "$REPO_DIR/update.sh" --version "$VERSION" > "$LOG" 2>&1
else
    bash "$REPO_DIR/update.sh" --rollback > "$LOG" 2>&1
fi
RC=$?
set -e

TO_VERSION="$(cat "$REPO_DIR/VERSION" 2>/dev/null || echo unknown)"
STATE="succeeded"
(( RC == 0 )) || STATE="failed"
ROLLED_BACK="no"
grep -q "Rollback complete" "$LOG" && ROLLED_BACK="yes"
FINISHED="$(ts)"
write_json "$OUT_DIR/status.json" "id=$REQ_ID" "action=$ACTION" "version=${VERSION:-null}" \
    "state=$STATE" "started_at=$STARTED" "finished_at=$FINISHED" "exit_code=$RC" \
    "rolled_back=$ROLLED_BACK" \
    "from_version=$FROM_VERSION" "to_version=$TO_VERSION" "requested_by=$REQ_BY" "reason=$REASON"
python3 - "$OUT_DIR/status.json" "$OUT_DIR/history.jsonl" <<'PY'
import json, sys
with open(sys.argv[2], "a") as f:
    f.write(json.dumps(json.load(open(sys.argv[1]))) + "\n")
PY
CAN_ROLLBACK="no"
[[ -f "$STATE_DIR/last-update.state" ]] && CAN_ROLLBACK="yes"
write_json "$OUT_DIR/agent.json" "last_seen=$(ts)" "every_minutes=1" \
    "agent_version=$AGENT_VERSION" "can_rollback=$CAN_ROLLBACK"
info "Finished $ACTION: $STATE (exit $RC), v$FROM_VERSION -> v$TO_VERSION"
