# NetWatch
> Self-hosted network diagnostics: find where and when connectivity fails, with evidence.

![Version](https://img.shields.io/badge/version-0.1.0-blue)

## Overview
NetWatch continuously probes your gateway, mesh nodes, public anchors and chosen devices,
stores the results locally, and graphs latency and loss SmokePing-style so intermittent
drops can be pinned to a time and a network layer. It runs as a single Docker container on
the always-on TrueNAS host. No data leaves your network apart from the probes themselves.

Built in phases: **Phase 1 (this release)** delivers probes, storage and graphs. Outage
detection, path tests, discovery, the root-cause diagnosis engine, the admin panel with
in-app updates, and the Wi-Fi client agent follow in later releases.

## Features
- **Probes:** ICMP (from every 1 s), DNS query latency/failures, TCP connect, and HTTP checks,
  each with its own interval and timeout.
- **Sensible defaults on first run:** auto-detected default gateway, Cloudflare 1.1.1.1,
  Google 8.8.8.8, and a DNS comparison of gateway vs 1.1.1.1 vs 8.8.8.8.
- **SmokePing-style graphs:** median line, p5–p95 and p25–p75 "smoke", and packet-loss bars,
  from 15 minutes to 90 days. Periods without data show as gaps.
- **Live overview:** health summary and status tiles grouped as gateway, mesh, internet,
  DNS, devices and custom.
- **Run test now** for any target, plus CSV/JSON export of raw samples.
- **Efficient storage:** SQLite (WAL) with raw samples kept 7 days and 1-minute rollups kept
  90 days (both configurable). Measured at about 0.6 % CPU and 60 MB RAM with 7 targets.
- **Operations:** `/health` endpoint, structured JSON logs, optional Prometheus `/metrics`.
- **Safe updates:** `update.sh` backs up the database, health-checks the new version and
  rolls back automatically if it fails.

## Requirements
- TrueNAS SCALE 24.10 (Electric Eel) or later, or any Linux host with Docker Engine 25+ and
  the Docker Compose plugin 2.24+
- `git` on the host
- Port **8085** free on the host (registered in the dev-sop port registry)

## Setup
`setup.sh` is the first-run script. It is idempotent, so it is safe to re-run.

### TrueNAS deployment
```bash
ssh truenas
cd /mnt/shared_vol/websites
git clone https://github.com/parabyte-ca/netwatch.git
cd netwatch
sudo ./setup.sh            # pulls ghcr.io/parabyte-ca/netwatch, falls back to a local build
# or: sudo ./setup.sh --build   to always build locally
```
Then open `http://192.168.2.15:8085`.

What `setup.sh` does:
1. Creates `.env` from `.env.example` if it doesn't exist.
2. Creates `data/` and `data/backups/`, owned by root.
3. Refuses to start if another process already holds the port.
4. Pulls the image for the version in `VERSION` (or builds it).
5. Starts the container with `docker compose`.
6. Waits for the health check to pass.

**Paths on TrueNAS**

| Path | Purpose |
|---|---|
| `/mnt/shared_vol/websites/netwatch/` | Git checkout, `docker-compose.yml`, `.env` |
| `/mnt/shared_vol/websites/netwatch/data/netwatch.db` | SQLite database (bind-mounted at `/data`) |
| `/mnt/shared_vol/websites/netwatch/data/backups/` | Pre-update database backups (last 10 kept) |

**Container security model**

| Setting | Why |
|---|---|
| `network_mode: host` | Probes leave from the host's real NIC; later phases need ARP, mDNS, SSDP and NIC counters. |
| `cap_drop: ALL` + `cap_add: NET_RAW, NET_ADMIN` | Raw sockets for ICMP and packet capture, and interface statistics. Nothing else. |
| `no-new-privileges`, `read_only: true`, `tmpfs /tmp` | No privilege escalation; only `/data` is writable. |
| Runs as root inside the container | Needed so the added capabilities apply (with `no-new-privileges`, file capabilities on a non-root binary are ignored). Without `CAP_DAC_OVERRIDE`, root can still only write where it is the owner, which is why `data/` is root-owned. |
| `mem_limit: 512m` | Guard rail; typical usage is about 60 MB. |

If the data directory sits on a dataset with restrictive NFSv4 ACLs and the container
reports `unhealthy` with a writer error, make sure `owner@` has full control of `data/`.

**Restricting to the LAN only:** set `NETWATCH_BIND=192.168.2.15` in `.env` and re-run
`sudo ./setup.sh`. Phase 1 has no login, so don't expose it through Cloudflare Tunnel or
Nginx Proxy Manager. Authentication arrives in Phase 5.

**GHCR access:** a new GHCR package is private by default. Either make
`ghcr.io/parabyte-ca/netwatch` public (GitHub → Packages → netwatch → Settings), run
`docker login ghcr.io` on TrueNAS with a read-only token, or simply let the scripts build
locally.

### Local development
```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e "backend[dev]"
NETWATCH_DATA_DIR=./data NETWATCH_ICMP_PRIVILEGED=false NETWATCH_LOG_FORMAT=text python -m netwatch

cd frontend && npm ci && npm run dev    # http://localhost:5173, proxies /api to :8085
```
Checks (the same ones CI runs): `ruff check . && ruff format --check . && pytest` in
`backend/`, `npm run typecheck && npm test && npm run build` in `frontend/`, and
`shellcheck -x setup.sh update.sh scripts/common.sh`.

Unprivileged ICMP needs `net.ipv4.ping_group_range` to include your group on the dev
machine. The container uses privileged raw sockets instead.

## Update
`update.sh` pulls the latest code and redeploys:
```bash
ssh truenas "cd /mnt/shared_vol/websites/netwatch && sudo ./update.sh"
```

| Command | Effect |
|---|---|
| `sudo ./update.sh` | Track `main` and the `:latest` image |
| `sudo ./update.sh --version 0.2.0` | Deploy a specific release tag |
| `sudo ./update.sh --build` | Build locally instead of pulling |
| `sudo ./update.sh --rollback` | Undo the last update (code, image and database) |

Each update:
1. Refuses to run if tracked files have local changes.
2. Tags the running image as `:previous`.
3. Takes an online, WAL-consistent database backup.
4. Fetches the new code, then pulls or builds the image.
5. Restarts the container and waits for `/health`.

If the new version isn't healthy within 3 minutes, `update.sh` restores the previous code,
image and database automatically. It also saves the failed container's logs to
`data/backups/`.

Git commands run as the owner of the checkout, so `sudo` never leaves root-owned files in
the repo.

## Configuration
Settings come from environment variables in `.env`; see `.env.example`. Re-run
`sudo ./setup.sh` after changing them.

| Variable | Default | Description |
|---|---|---|
| `NETWATCH_PORT` | `8085` | Web UI / API port on the host |
| `NETWATCH_BIND` | `0.0.0.0` | Bind address; set to the LAN IP to restrict access |
| `NETWATCH_RAW_RETENTION_DAYS` | `7` | Days of raw per-probe samples (1–90) |
| `NETWATCH_ROLLUP_RETENTION_DAYS` | `90` | Days of 1-minute rollups (7–730) |
| `NETWATCH_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `NETWATCH_LOG_FORMAT` | `json` | `json` (structured) or `text` |
| `NETWATCH_METRICS_ENABLED` | `false` | Expose Prometheus metrics at `/metrics` |
| `NETWATCH_ICMP_PRIVILEGED` | `true` | Raw-socket ICMP (needs `NET_RAW`) |
| `NETWATCH_MAX_CONCURRENT_PROBES` | `64` | Upper bound on probes in flight |
| `NETWATCH_MAX_TARGETS` | `200` | Maximum number of targets |
| `NETWATCH_SEED_DEFAULTS` | `true` | Create the default targets on first run |
| `TZ` | `America/Toronto` | Container time zone (logs) |

**Probe intervals:** ICMP 1–3600 s, DNS and TCP 5–3600 s, HTTP 10–3600 s. The timeout
defaults to the smaller of the interval and 2 s (ICMP), 3 s (DNS), 5 s (TCP) or 10 s
(HTTP).

**HTTP API** (JSON, no auth in Phase 1):

| Endpoint | Purpose |
|---|---|
| `GET /health` | Health: database, scheduler and writer checks (503 when unhealthy) |
| `GET /api/system` | Version, default route, database size, retention |
| `GET /api/status` | Live state per target (up / degraded / down / no data) |
| `GET, POST /api/targets` | List or create targets |
| `GET, PUT, DELETE /api/targets/{id}` | Read, replace or delete a target (delete removes its history) |
| `POST /api/targets/{id}/test` | Run one probe now (result not stored) |
| `GET /api/targets/{id}/series?start=&end=&points=` | Graph data; raw samples for ranges of 6 h or less, rollups beyond that |
| `GET /api/targets/{id}/export?start=&end=&format=csv\|json` | Raw samples, up to 31 days |
| `GET /metrics` | Prometheus metrics (when enabled) |

## Changelog
See [CHANGELOG.md](CHANGELOG.md).

## License
MIT. See [LICENSE](LICENSE).
