#!/usr/bin/env bash
# upgrade-agent.sh: host-side helper that performs in-app update requests for NetWatch.
#
# The container cannot (and should not) update itself. When you press "Update" in the web UI,
# NetWatch writes upgrade/request.json. This agent, run every minute by cron as root, picks the
# request up and runs update.sh, which backs up the database, health-checks the new version and
# rolls back automatically on failure. Progress is reported back through files in upgrade/.
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

AGENT_VERSION="1"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UPGRADE_DIR="$REPO_DIR/upgrade"
SCRIPT_PATH="$REPO_DIR/scripts/upgrade-agent.sh"
CRON_DESC="NetWatch upgrade agent"
CRON_FILE="/etc/cron.d/netwatch-upgrade-agent"
AGENT_LOG="$UPGRADE_DIR/agent.log"

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

install_cron() {
    mkdir -p "$UPGRADE_DIR"
    chmod 700 "$UPGRADE_DIR"
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
    for f in agent.json status.json request.json; do
        [[ -f "$UPGRADE_DIR/$f" ]] && echo "$f: $(cat "$UPGRADE_DIR/$f")"
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

mkdir -p "$UPGRADE_DIR"
chmod 700 "$UPGRADE_DIR"

# Keep our own log small.
if [[ -f "$AGENT_LOG" ]] && (( $(stat -c %s "$AGENT_LOG") > 1048576 )); then
    mv -f "$AGENT_LOG" "$AGENT_LOG.1"
fi

exec 9>"$UPGRADE_DIR/.lock"
flock -n 9 || exit 0  # a previous pass is still running an update

# Atomically write a JSON file from KEY=VALUE pairs (values are strings; "null"/ints converted).
write_json() {
    local dest="$1"; shift
    python3 - "$dest" "$@" <<'PY'
import json, os, sys
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
tmp = f"{dest}.tmp.{os.getpid()}"
with open(tmp, "w") as f:
    json.dump(data, f)
os.replace(tmp, dest)
PY
}

write_json "$UPGRADE_DIR/agent.json" "last_seen=$(ts)" "every_minutes=1" "agent_version=$AGENT_VERSION"

# A "running" status while we hold the lock means a previous pass died mid-update.
if [[ -f "$UPGRADE_DIR/status.json" ]] && grep -q '"state": "running"' "$UPGRADE_DIR/status.json"; then
    python3 - "$UPGRADE_DIR/status.json" "$(ts)" <<'PY'
import json, sys
path, now = sys.argv[1], sys.argv[2]
st = json.load(open(path))
st.update(state="failed", finished_at=now, error="agent interrupted during the update")
json.dump(st, open(path, "w"))
PY
fi

REQUEST="$UPGRADE_DIR/request.json"
[[ -f "$REQUEST" && ! -L "$REQUEST" ]] || exit 0

# Validate the request strictly; nothing from it reaches a shell unchecked.
if ! PARSED="$(python3 - "$REQUEST" <<'PY'
import json, re, sys
try:
    r = json.load(open(sys.argv[1]))
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
    rm -f "$REQUEST"
    exit 0
fi
read -r REQ_ID ACTION VERSION REQ_BY REASON <<< "$PARSED"
[[ "$VERSION" == "-" ]] && VERSION=""
rm -f "$REQUEST"

FROM_VERSION="$(cat "$REPO_DIR/VERSION" 2>/dev/null || echo unknown)"
STARTED="$(ts)"
info "Starting $ACTION ${VERSION:+to v$VERSION }(request $REQ_ID by $REQ_BY, $REASON)"
write_json "$UPGRADE_DIR/status.json" "id=$REQ_ID" "action=$ACTION" "version=${VERSION:-null}" \
    "state=running" "started_at=$STARTED" "from_version=$FROM_VERSION" \
    "requested_by=$REQ_BY" "reason=$REASON"

[[ -f "$UPGRADE_DIR/upgrade.log" ]] && mv -f "$UPGRADE_DIR/upgrade.log" "$UPGRADE_DIR/upgrade.log.1"
set +e
if [[ "$ACTION" == "update" ]]; then
    bash "$REPO_DIR/update.sh" --version "$VERSION" > "$UPGRADE_DIR/upgrade.log" 2>&1
else
    bash "$REPO_DIR/update.sh" --rollback > "$UPGRADE_DIR/upgrade.log" 2>&1
fi
RC=$?
set -e

TO_VERSION="$(cat "$REPO_DIR/VERSION" 2>/dev/null || echo unknown)"
STATE="succeeded"
(( RC == 0 )) || STATE="failed"
ROLLED_BACK="no"
grep -q "Rollback complete" "$UPGRADE_DIR/upgrade.log" && ROLLED_BACK="yes"
FINISHED="$(ts)"
write_json "$UPGRADE_DIR/status.json" "id=$REQ_ID" "action=$ACTION" "version=${VERSION:-null}" \
    "state=$STATE" "started_at=$STARTED" "finished_at=$FINISHED" "exit_code=$RC" \
    "rolled_back=$ROLLED_BACK" \
    "from_version=$FROM_VERSION" "to_version=$TO_VERSION" "requested_by=$REQ_BY" "reason=$REASON"
python3 - "$UPGRADE_DIR/status.json" "$UPGRADE_DIR/history.jsonl" <<'PY'
import json, sys
with open(sys.argv[2], "a") as f:
    f.write(json.dumps(json.load(open(sys.argv[1]))) + "\n")
PY
info "Finished $ACTION: $STATE (exit $RC), v$FROM_VERSION -> v$TO_VERSION"
