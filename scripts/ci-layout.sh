#!/usr/bin/env bash
# CI phone layout check: seed a throwaway install, start it, and check every page at 390 px
# (normal and 1.3x text). Run from the repo root after `npm run build` in frontend/ and
# `pip install -e backend`. Needs Playwright's Chromium (npx playwright install chromium).
set -euo pipefail

port="${LAYOUT_PORT:-8090}"
data="$(mktemp -d)"
log="$data/server.log"
base="http://127.0.0.1:$port"
password="ci-layout-$(head -c 12 /dev/urandom | od -An -tx1 | tr -d ' \n')"

# shellcheck disable=SC2329 # invoked by the EXIT trap
cleanup() {
  if [[ -n "${pid:-}" ]]; then kill "$pid" 2>/dev/null || true; fi
  rm -rf "$data"
}
trap cleanup EXIT

paths="$(cd backend && python scripts/ci_seed.py "$data/nw")"

(
  cd backend
  NETWATCH_DATA_DIR="$data/nw" NETWATCH_STATIC_DIR="$PWD/../frontend/dist" \
    NETWATCH_PORT="$port" NETWATCH_BIND=127.0.0.1 NETWATCH_ICMP_PRIVILEGED=false \
    exec python -m netwatch
) >"$log" 2>&1 &
pid=$!

for _ in $(seq 60); do
  curl -fsS "$base/health" >/dev/null 2>&1 && break
  if ! kill -0 "$pid" 2>/dev/null; then cat "$log"; exit 1; fi
  sleep 1
done
curl -fsS "$base/health" >/dev/null || { echo "NetWatch did not start"; cat "$log"; exit 1; }

token="$(tr -d '\n' <"$data/nw/setup-token")"
curl -fsS -o /dev/null -X POST "$base/api/auth/setup" \
  -H 'Content-Type: application/json' -H 'X-Requested-With: netwatch' \
  -d "{\"username\":\"admin\",\"password\":\"$password\",\"setup_token\":\"$token\"}"

status=0
for scale in 1 1.3; do
  echo "== text scale $scale"
  # shellcheck disable=SC2086 # one argument per device page
  TEXT_SCALE="$scale" node frontend/scripts/check-layout.mjs "$base" admin "$password" $paths \
    || status=1
done
if [[ "$status" -ne 0 ]]; then
  echo "== server log (last 40 lines)"
  tail -n 40 "$log"
fi
exit "$status"
