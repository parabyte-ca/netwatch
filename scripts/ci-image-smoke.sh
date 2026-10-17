#!/usr/bin/env bash
# CI smoke test for a freshly built image (run by .github/workflows/ci.yml before pushing):
#   1. mtr and iperf3 work inside the container with only NET_RAW/NET_ADMIN, and NetWatch's own
#      parsers understand their real output; raw packet capture (discovery) works with the
#      kernel filter attached, and the offline vendor registry is present;
#   2. the app starts healthy with the production hardening (read-only root, caps dropped).
# Usage: scripts/ci-image-smoke.sh <image>
set -euo pipefail

IMAGE="${1:?usage: ci-image-smoke.sh <image>}"
HARDENING=(--network host --cap-drop ALL --cap-add NET_RAW --cap-add NET_ADMIN --security-opt no-new-privileges)

echo "==> Tools inside the image"
docker run --rm "${HARDENING[@]}" --entrypoint sh "$IMAGE" -c '
set -e
mtr --version
iperf3 --version | head -1
iperf3 -s -D -1 -p 5299
sleep 1
iperf3 -c 127.0.0.1 -p 5299 -t 1 -J > /tmp/iperf.json
mtr --json --no-dns --report-cycles 2 --interval 0.2 127.0.0.1 > /tmp/mtr.json
python - <<PY
from netwatch.services.pathtest import parse_mtr_json, summarise
from netwatch.services.throughput import parse_iperf_json

r = parse_iperf_json(open("/tmp/iperf.json").read())
assert r["bits_per_second"] > 0, r
hops = parse_mtr_json(open("/tmp/mtr.json").read())
s = summarise(hops)
assert s["reached"] and s["first_loss_hop"] is None, (hops, s)
print("iperf3 loopback %.1f Gb/s; mtr %d hop(s), reached" % (r["bits_per_second"] / 1e9, s["hop_count"]))

import socket, time
from netwatch.services.discovery.capture import Sniffer
from netwatch.services.discovery.classify import vendor_for

got = []
cap = Sniffer("lo", lambda ts, frame, out: got.append(frame))
cap.start()
assert cap.running, cap.error
with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as u:
    u.sendto(b"dropped-by-filter", ("127.0.0.1", 9))
    u.sendto(b"NOTIFY * HTTP/1.1\r\n\r\n", ("127.0.0.1", 1900))
deadline = time.time() + 3
while time.time() < deadline and not any(b"NOTIFY" in f for f in got):
    time.sleep(0.05)
cap.stop()
assert any(b"NOTIFY" in f for f in got) and not any(b"dropped-by-filter" in f for f in got), got
assert vendor_for("00:11:32:aa:bb:cc") == "Synology"
print("raw capture with kernel filter: ok; vendor registry: ok")
PY
'

echo "==> App starts healthy with production hardening"
DATA="$(mktemp -d)"
# The container's root has no CAP_DAC_OVERRIDE, so it can only write where permissions allow;
# on TrueNAS setup.sh makes data/ root-owned, here the runner's temp dir must be opened up.
chmod 777 "$DATA"
docker run -d --name netwatch-smoke "${HARDENING[@]}" --read-only --tmpfs /tmp \
    -e NETWATCH_PORT=8099 -e NETWATCH_UPDATE_CHECK=false -v "$DATA:/data" "$IMAGE" >/dev/null
trap 'docker rm -f netwatch-smoke >/dev/null 2>&1 || true' EXIT
for _ in $(seq 1 30); do
    if curl -fsS http://127.0.0.1:8099/health >/dev/null 2>&1; then
        curl -fsS http://127.0.0.1:8099/health; echo
        exit 0
    fi
    sleep 2
done
docker logs netwatch-smoke | tail -40
echo "app did not become healthy" >&2
exit 1
