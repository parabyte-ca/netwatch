# NetWatch
> Self-hosted network diagnostics: find where and when connectivity fails, with evidence.

![Version](https://img.shields.io/badge/version-0.9.2-blue)

## Overview
NetWatch continuously probes your gateway, mesh nodes, public anchors and chosen devices,
stores the results locally, and graphs latency and loss SmokePing-style so intermittent
drops can be pinned to a time and a network layer. It runs as a single Docker container on
the always-on TrueNAS host. No data leaves your network apart from the probes themselves, the
nightly bufferbloat test's download and upload to Cloudflare's speed-test servers, a daily
update check against GitHub (both can be switched off), and, only if you turn on Ask NetWatch
with your own Anthropic API key, the data Claude looks up to answer your questions.

Built in phases: **v0.1** probes, storage and graphs; **v0.2** sign-in and web/automatic
updates; **v0.3** outage detection, an event timeline, TrueNAS NIC telemetry, mtr path tests
and iperf3 throughput tests; **v0.4** device discovery, an inventory and network anomaly
alerts; **v0.5** the root-cause diagnosis engine and an incident report for Bell; **v0.6** the
admin panel; **v0.7** the network map with incident replay and the Deco mesh integration;
**v0.8** live per-device bandwidth, the bufferbloat grade and the Home Hub integration;
**v0.9 (this release)** the exposure scan, the network health score with weekly reports, and Ask
NetWatch. See the [Roadmap](#roadmap) for what's next.

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
- **Outage detection with hysteresis:** a target is *down* after 3 consecutive failed probes and
  only recovers after 3 consecutive successes, so single lost pings never open an outage;
  intermittent loss above 5 % (clearing at 1 %) is *packet loss*. Outages are timestamped from
  the first failure to the first success, and shaded on the latency graphs.
- **Event timeline:** outages, packet loss, NIC problems and NetWatch restarts by day, each with
  a detail page showing the evidence: latency around the event, what else failed at the same
  time (±5 min), TrueNAS NIC events, and path tests.
- **TrueNAS NIC telemetry:** link down/up, flaps too short to see between polls (from the
  kernel's carrier-change counter), speed/duplex renegotiation and error/CRC counters, on the
  default-route interface and the physical ports under a bridge, bond or VLAN.
- **Path tests (mtr):** public targets are traced every 15 minutes and automatically when an
  outage starts; the result shows the first hop from which loss persists to the destination
  (ignoring silent hops and ICMP rate-limiting) and flags route changes.
- **Throughput tests (iperf3):** LAN (TrueNAS → a device running `iperf3 -s`) and WAN (a public
  iperf3 server you add), download and upload, on demand or on a schedule, to separate LAN
  problems from Bell.
- **Device discovery (passive, on by default):** NetWatch listens for the ARP, DHCP, mDNS and
  SSDP broadcasts devices send anyway, plus TrueNAS's own ARP cache, and never transmits
  anything in this mode. A kernel packet filter drops all other traffic before it reaches
  NetWatch, so NAS transfers cost nothing.
- **Active scanning (on by default, hourly; switch off on the Devices page):** an ARP sweep of
  the local subnet (50 packets/s), one DHCP server probe (a DISCOVER that never takes a lease),
  SSDP and mDNS queries, and reverse DNS through the gateway. Private subnets only, at most a
  /22.
- **Device inventory:** vendor from the offline IEEE registry, names from DHCP, mDNS, UPnP and
  reverse DNS, an explainable type guess (router, Deco access point, phone, TV, printer…), IP
  address history, and online/last-seen. Phones' private (randomised) Wi-Fi MACs that report
  the same hostname are merged into one device; you can merge or split by hand, rename,
  override the type and add notes.
- **Monitor this device:** one click creates a ping target that follows the device when its IP
  address changes.
- **Anomaly alerts** on the timeline, each with the evidence and the usual cause: duplicate IP
  addresses, a second DHCP server (e.g. a Deco that fell out of AP mode), ARP storms (switching
  loops), gateway MAC changes, devices repeatedly rejoining the network (Wi-Fi drops), IPs
  that keep changing, double NAT and carrier-grade NAT (from path tests), resolvers that
  disagree, and resolvers that answer for non-existent names. "New device" events start after
  a one-hour learning period (private-MAC devices are not announced, as they rotate).
- **Diagnosis engine:** failures that overlap are grouped into incidents, and each incident is
  explained by small, explainable rules that compare what failed with what kept working, along a
  map of the network (TrueNAS link → switch → Home Hub → Bell → internet, with the Decos and
  devices behind the Hub). Every likely cause has a confidence, the evidence it used and next
  steps: TrueNAS link problems, the switch/cabling or a Hub restart, a Bell outage (strengthened
  when a path test shows loss starting beyond the Hub), one destination only, Home Hub DNS, a
  single Deco or the whole mesh, one device, IP conflicts, switching loops and rogue DHCP.
- **Network map:** an interactive map of the home network (Internet → Bell → Home Hub → switch
  → TrueNAS, the main Deco and its satellites, and the devices on each Deco). Monitored and
  named devices are nodes; the rest are counted per Deco and listed when you tap the group.
  Links show median latency and are coloured by packet loss, Wi-Fi links are dashed, and tapping
  a node opens its details. The layout is automatic; drag nodes to tidy it (saved, with a reset).
- **Incident replay:** move a time slider or pick an incident to see the map as it was, with the
  likely cause and the failing nodes highlighted ("Show on map" from Diagnosis and events).
- **Deco mesh integration (optional):** reads the Decos' local API with the owner password to
  learn each unit's state and backhaul and which device is on which Deco and band. It adds
  Wi-Fi events to the timeline (Deco offline/online, backhaul changes, roaming and Wi-Fi
  disconnects over a minute for monitored devices) and diagnosis causes (a Deco went offline,
  every failing device was on one Deco, a device dropped off Wi-Fi). The password is encrypted
  with a key file kept outside the database, so backups never contain it.
- **Live bandwidth per device:** who is using the internet right now and over the last day or
  week ("top talkers" on the Overview and the map), live traffic on every map link (busy links
  are thicker and animated), and a bandwidth chart on each device page. Measured by the Decos,
  kept at 1-minute detail for 7 days (adjustable under Admin → settings), then as hourly totals.
- **Bufferbloat grade (A+ to F):** latency to 1.1.1.1 while the line is idle, then while it is
  saturated downloading and uploading, graded like Waveform's test. It explains the classic
  "video calls lag whenever someone downloads". Runs nightly after the update window (can be
  switched off) and on demand from Diagnostics; tests never raise packet-loss alerts.
- **Home Hub integration (Bell Home Hub 4000):** reads the Hub's own view of the line every
  minute: restarts (from its uptime), fibre receive/transmit levels and link state, and the
  internet session. Restarts, link-down periods, session reconnects and a weak fibre signal
  become timeline events and top-confidence diagnosis causes, and the Bell incident report gains
  a "Bell line" section. Read-only; an advanced explorer shows the Hub's raw data with
  passwords hidden.
- **Exposure scan:** every night (and on demand) NetWatch checks about 30 well-known ports on
  each LAN device with a plain TCP connection and reads the Home Hub's port forwards, UPnP
  mappings and DMZ. Findings are ranked: a risky service forwarded to the internet (RDP, SMB,
  Telnet…) or an open DMZ is critical; Telnet, an open Docker API, Redis and similar are flagged
  even on the LAN. New open services and new port forwards (including ones a device opened
  itself through UPnP) appear on the timeline. Each device page lists its open services.
- **Network health score (0-100):** one number for the last 24 hours, 7 or 30 days, built from
  internet uptime, connection quality, latency under load, the home network, the fibre line
  and exposure, with what is dragging it down. Shown on the Overview and its own Health page.
  Areas without data (no Home Hub, no test yet) are left out rather than counted against you.
- **Weekly health report:** written every Monday morning for the week before, with the score
  and how it changed, highlights, the breakdown, availability, speed tests and incidents; kept
  for two years and downloadable as Markdown.
- **Ask NetWatch:** ask about the network in plain language ("was the internet down this week,
  and whose fault was it?", "who is using the bandwidth?") from a chat panel on every page.
  Claude answers from NetWatch's own data through read-only tools (live status, timeline,
  diagnosis, ISP report, devices, bandwidth, health score, exposure, the Bell line) and can't
  change anything. Off until you save an Anthropic API key under Admin; uses Claude Opus 5.5 by
  default (Sonnet or Haiku for lower cost), limited to 40 questions an hour.
- **Patterns:** failures that repeat at a fixed interval (e.g. every 6 h) or cluster at a time of
  day are called out, since both point at something scheduled.
- **Incident report for Bell:** a printable report (Print / save as PDF, or download Markdown)
  with the incidents, their evidence and path tests, what was ruled out ("the Home Hub stayed
  reachable during 5 of 5 outages"), availability, latency per target and speed tests. It can be
  limited to incidents that point at Bell.
- **Admin panel:** every tunable setting in the web UI (detection thresholds, path tests,
  discovery subnets and rates, NIC, retention, sessions and lockout, metrics, log level), with
  defaults and "reset to default". Values saved there override `.env`; most apply immediately,
  the few that can't show a **Restart now** banner (NetWatch restarts itself in a few seconds).
- **Hardened sign-in:** list of signed-in sessions with address and browser, sign out any or all
  others, configurable lockout after failed sign-ins, and a security log (sign-ins, failures,
  lockouts, password and settings changes, backups and restores).
- **Backup and restore from the web page:** consistent snapshots on demand (and before every
  update), download, delete, and restore from a stored backup or an uploaded `.db`/`.db.gz`. A
  restore is checked (intact, a NetWatch database, not from a newer version), then applied on a
  controlled restart after a safety copy of the current database is taken; older backups are
  migrated forward automatically.
- **Efficient storage:** SQLite (WAL) with raw samples kept 7 days and 1-minute rollups kept
  90 days (both configurable). Measured at about 0.6 % CPU and 60 MB RAM with 7 targets.
- **Operations:** `/health` endpoint, structured JSON logs, optional Prometheus `/metrics`.
- **Sign-in required:** first visit creates the admin account with a one-time setup code (so
  nobody else on the LAN can claim it first); every page and API call needs a session after
  that. Passwords are stored as scrypt hashes, sessions are server-side HttpOnly cookies that
  end after 7 days without use, and repeated failed logins lock out for 5 minutes.
- **Updates from the web UI:** see the running and latest versions, read the release notes, and
  press **Install**. A small host-side agent runs `update.sh`, which backs up the database,
  health-checks the new version and rolls back automatically if it fails. Live log, update
  history and a **Roll back** button are on the System page.
- **Automatic updates:** bug-fix (patch) releases install automatically in a nightly
  maintenance window (03:30 by default); feature and major releases wait for you to press
  Install. Configurable: off, notify only, patch, or patch + minor.

## Requirements
- TrueNAS SCALE 24.10 (Electric Eel) or later, or any Linux host with Docker Engine 25+ and
  the Docker Compose plugin 2.24+
- `git` on the host
- Port **8090** free on the host (registered in the dev-sop port registry; check with `sudo ss -tlnp | grep 8090`)

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
Then open `http://192.168.2.15:8090` and create the admin account with the **setup code**
`setup.sh` prints (also in `data/setup-token` and `sudo docker logs netwatch`).

If you open NetWatch by a name rather than its IP address (e.g. `truenas.local`), add that name
to `NETWATCH_ALLOWED_HOSTS` in `.env` and run `sudo docker compose up -d`; names not listed are
refused to block DNS-rebinding attacks, and the page explains what to add.

What `setup.sh` does:
1. Creates `.env` from `.env.example` if it doesn't exist.
2. Creates `data/`, `data/backups/` and the upgrade agent folders, owned by root.
3. Refuses to start if another process already holds the port.
4. Pulls the image for the version in `VERSION` (or builds it).
5. Starts the container with `docker compose`.
6. Waits for the health check to pass.
7. Installs the upgrade agent as a TrueNAS cron job (skip with `--no-agent`).

**Paths on TrueNAS**

| Path | Purpose |
|---|---|
| `/mnt/shared_vol/websites/netwatch/` | Git checkout, `docker-compose.yml`, `.env` |
| `/mnt/shared_vol/websites/netwatch/data/netwatch.db` | SQLite database (bind-mounted at `/data`) |
| `/mnt/shared_vol/websites/netwatch/data/backups/` | Pre-update database backups (last 10 kept) |
| `/mnt/shared_vol/websites/netwatch/data/.secret_key` | Key for stored credentials (Deco password); not in backups, so if it is lost just enter the password again |
| `/mnt/shared_vol/websites/netwatch/upgrade/` | Update requests from the container to the upgrade agent (the only folder it may write) |
| `/mnt/shared_vol/websites/netwatch/upgrade-status/` | The agent's status, update log and history (read-only inside the container) |
| `/mnt/shared_vol/websites/netwatch/.state/` | Root-only: the last update's rollback record, the agent's lock, log and rollback logs |

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
`sudo ./setup.sh`. Login is required for everything, but NetWatch is still meant for the LAN;
don't expose it through Cloudflare Tunnel without HTTPS (and then set
`NETWATCH_COOKIE_SECURE=true`).

**First-run account:** until the admin account exists, anyone who can reach the page can
create it, so do the first visit right after `setup.sh` finishes.

**Forgotten password:** reset it from TrueNAS (this signs out every session):
```bash
sudo docker exec -it netwatch python -m netwatch.reset_password admin
```

**GHCR access:** a new GHCR package is private by default. Either make
`ghcr.io/parabyte-ca/netwatch` public (GitHub → Packages → netwatch → Settings), run
`docker login ghcr.io` on TrueNAS with a read-only token, or simply let the scripts build
locally.

### LAN throughput tests
To measure the LAN, run an iperf3 server on the device at the far end and add it under
**Diagnostics → Throughput**:
- Windows PC: download iperf3, then `iperf3.exe -s` (allow it through the firewall, TCP 5201).
- Phone: an iperf3 app in server mode (e.g. "iPerf3" / "Magic iPerf"), connected to a Deco.

A wired PC that tests fast while Wi-Fi devices test slow points at the mesh; a slow wired test
points at the switch or cabling; a fast LAN with a slow WAN test points at Bell.

### Deco mesh
Under **Admin → Deco mesh**, enter the main Deco's IP address and the Deco **owner** password,
press **Test connection**, then tick **Poll every 30 s** and **Save**.

Deco only lets the owner account sign in locally, and an owner sign-in in the Deco app signs
NetWatch out (it signs in again on its next poll). To avoid that tug-of-war, invite a second
account as a **manager** in the Deco app (Me → Managers) and use that one on your phone; leave
the owner account to NetWatch. Only LAN addresses are accepted, so the password never leaves
the network.

### Home Hub
Under **Admin → Home Hub**, check the address (NetWatch suggests your gateway, normally
`192.168.2.1`), enter the Hub's **admin** password (the one for its web page; by default it's
printed on the Hub's label), press **Test connection**, then tick **Poll every minute** and
**Save**. The test reports the model, firmware, uptime and fibre level.

The Hub's API is undocumented and firmware-dependent; NetWatch detects the sign-in method
(MD5 or SHA-512) itself. If a value such as the fibre level shows as missing, open
**Advanced: explore the Hub's data** (e.g. `Device/Optical/Interfaces`) and send the output
(passwords and keys are hidden) so the mapping can be adjusted. After 3 failed sign-ins NetWatch
stops trying until you save the settings again, so a changed password can't lock the Hub.

### Ask NetWatch
Create an API key at [console.anthropic.com](https://console.anthropic.com) (usage is billed to
that account), then under **Admin → Ask NetWatch** paste it, pick the model and effort, press
**Test key** and **Save**. An **Ask NetWatch** button appears at the bottom right of every page.
The key is encrypted like the Hub and Deco passwords. Each question sends the question and the
data Claude looks up (target and device names and addresses, incident history) to Anthropic.

### Local development
```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e "backend[dev]"
NETWATCH_DATA_DIR=./data NETWATCH_ICMP_PRIVILEGED=false NETWATCH_LOG_FORMAT=text python -m netwatch

cd frontend && npm ci && npm run dev    # http://localhost:5173, proxies /api to :8090
```
Checks (the same ones CI runs): `ruff check . && ruff format --check . && pytest` in
`backend/`, `npm run typecheck && npm test && npm run build` in `frontend/`, and
`shellcheck -x setup.sh update.sh scripts/common.sh`.

Unprivileged ICMP needs `net.ipv4.ping_group_range` to include your group on the dev
machine. The container uses privileged raw sockets instead.

## Update

### From the web UI (recommended)
Open **System**. NetWatch checks GitHub releases daily (or press **Check now**), shows the
release notes, and installs with **Install vX.Y.Z**. Progress and the full update log stream
into the page; NetWatch restarts and the page reloads itself on the new version.

How it works: the container never updates itself. It writes `upgrade/request.json`; the
**upgrade agent** (`scripts/upgrade-agent.sh`, run every minute by a TrueNAS cron job as root)
validates the request and runs `update.sh --version X.Y.Z`. Status, log and history come back
through `upgrade-status/`, which the container can only read.

The agent is root on TrueNAS, so it treats everything the container can write as hostile: it
only reads `upgrade/request.json` (no symlinks, size-capped, strictly validated, never a
downgrade), keeps its own files where the container cannot reach them, and `update.sh` works
on `data/` only from inside a throwaway container. A compromised NetWatch container therefore
cannot use the agent to touch files on the host.

| Agent command | Effect |
|---|---|
| `sudo scripts/upgrade-agent.sh --install` | Create the cron job (TrueNAS: via `midclt`, so it shows under System → Advanced → Cron Jobs and survives OS upgrades) |
| `sudo scripts/upgrade-agent.sh --status` | Show the cron job, heartbeat and last result |
| `sudo scripts/upgrade-agent.sh --uninstall` | Remove the cron job |

**Automatic updates** (System → Automatic updates):

| Policy | Behaviour |
|---|---|
| Off | No update checks |
| Notify only | Shows available updates; you press Install |
| Bug-fix releases automatically *(default)* | Patch releases (0.2.0 → 0.2.1) install within an hour after the window start (03:30 Toronto) |
| Bug-fix and feature releases automatically | Patch and minor releases install automatically; major releases always wait |

A failed automatic update is rolled back and not retried; the release stays available for a
manual install.

**Upgrading from v0.1.0** (one time, because v0.1.0 has no upgrade agent):
```bash
cd /mnt/shared_vol/websites/netwatch
sudo ./update.sh --version 0.2.0
sudo scripts/upgrade-agent.sh --install
```

### From the command line
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
2. Tags the running image as `:rollback-<version>`.
3. Takes an online, WAL-consistent database backup.
4. For a release, pulls the image and verifies its signature (see below) before touching the
   code; the release tag must be the commit the signed image was built from.
5. Checks out the new code (or pulls/builds `:latest` when tracking `main`).
6. Restarts the container and waits for `/health`.

If the new version isn't healthy within 3 minutes, `update.sh` restores the previous code,
image and database automatically, and saves the container's logs to
`.state/rollback-<timestamp>.log`. A rollback first saves the current database as one more
backup, so nothing collected since the update is lost for good.

**Signed releases.** CI signs every published image with [Sigstore](https://www.sigstore.dev/)
(keyless: the signature is tied to this repository's GitHub workflow, there is no key to
leak). `update.sh` checks it with cosign (run from its pinned official image) and refuses to
deploy anything else; because the tag must match the image's source commit, the scripts that
then run as root are exactly the ones CI built. Releases before v0.7.1 are unsigned. To deploy
a local build, use `--build`; to switch verification off (not recommended), set
`NETWATCH_VERIFY_IMAGES=false` in `.env`.

Git commands run as the owner of the checkout, so `sudo` never leaves root-owned files in
the repo.

### Releasing a new version
Releases are automatic. In the PR, bump `VERSION`, `backend/pyproject.toml`,
`frontend/package.json` and the README badge, and add the `CHANGELOG.md` section
(`tests/test_version.py` fails if they disagree). When the PR merges to `main`, CI sees a
`VERSION` with no git tag yet and:
1. publishes `ghcr.io/parabyte-ca/netwatch:X.Y.Z`, `:X.Y` and `:latest`;
2. creates the `vX.Y.Z` tag and a GitHub Release with that CHANGELOG section as notes.

Running instances then offer (or, for bug-fix releases, automatically install) the update.
Merges that don't change `VERSION` only refresh `:latest`. Pushing a `vX.Y.Z` tag by hand
still works and produces the same release.

## Configuration
Settings come from environment variables in `.env`; see `.env.example`. Re-run
`sudo ./setup.sh` after changing them. Most of them can also be changed on the **Admin** page;
a value saved there overrides `.env` until you press "reset to default". Port, bind address
and paths can only be set in `.env`.

| Variable | Default | Description |
|---|---|---|
| `NETWATCH_PORT` | `8090` | Web UI / API port on the host |
| `NETWATCH_BIND` | `0.0.0.0` | Bind address; set to the LAN IP to restrict access |
| `NETWATCH_RAW_RETENTION_DAYS` | `7` | Days of raw per-probe samples (1–90) |
| `NETWATCH_ROLLUP_RETENTION_DAYS` | `90` | Days of 1-minute rollups (7–730) |
| `NETWATCH_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `NETWATCH_LOG_FORMAT` | `json` | `json` (structured) or `text` |
| `NETWATCH_METRICS_ENABLED` | `false` | Expose Prometheus metrics at `/metrics` |
| `NETWATCH_METRICS_TOKEN` | *(empty)* | Bearer token for Prometheus to scrape `/metrics` without a login session |
| `NETWATCH_SESSION_DAYS` | `30` | Login session lifetime |
| `NETWATCH_SESSION_IDLE_HOURS` | `168` | Sign out a session unused for this long (0 = never) |
| `NETWATCH_COOKIE_SECURE` | `false` | Mark the session cookie Secure (only when served over HTTPS) |
| `NETWATCH_ALLOWED_HOSTS` | *(empty)* | Host names/addresses the UI may be opened by, comma-separated (e.g. `192.168.2.15`). Empty allows any IP address and `localhost`; other names are refused, which blocks DNS-rebinding attacks |
| `NETWATCH_VERIFY_IMAGES` | `true` | Verify release image signatures before updating (read by `update.sh`) |
| `NETWATCH_UPDATE_CHECK` | `true` | Daily check of GitHub releases (the only outbound call besides probes) |
| `NETWATCH_UPDATE_REPO` | `parabyte-ca/netwatch` | Repository whose releases are offered |
| `NETWATCH_AUTO_UPDATE` | `patch` | Initial policy: `off`, `notify`, `patch`, `minor` (then managed in the UI) |
| `NETWATCH_AUTO_UPDATE_WINDOW` | `03:30` | Initial maintenance window start, local time (then managed in the UI) |
| `NETWATCH_DOWN_AFTER` | `3` | Consecutive failed probes before a target is declared down |
| `NETWATCH_UP_AFTER` | `3` | Consecutive successful probes before it is declared recovered |
| `NETWATCH_DEGRADED_ENTER_PCT` | `5` | Loss % (over max(60 s, 10 probes)) that starts a packet-loss event |
| `NETWATCH_DEGRADED_EXIT_PCT` | `1` | Loss % at or below which it ends |
| `NETWATCH_EVENT_RETENTION_DAYS` | `365` | Days of timeline events |
| `NETWATCH_NIC_INTERFACES` | *(auto)* | Comma-separated interfaces to watch; default is the default-route interface plus its physical ports |
| `NETWATCH_NIC_POLL_S` | `2` | NIC polling interval |
| `NETWATCH_PATH_TEST_INTERVAL_MIN` | `15` | Scheduled mtr runs to public targets (0 disables) |
| `NETWATCH_PATH_TEST_CYCLES` | `10` | Probes per hop per mtr run |
| `NETWATCH_PATH_TEST_ON_OUTAGE` | `true` | Run mtr automatically when an outage starts (once per host per 5 min) |
| `NETWATCH_PATH_TEST_RETENTION_DAYS` | `90` | Days of path-test results |
| `NETWATCH_THROUGHPUT_RETENTION_DAYS` | `365` | Days of iperf3 results |
| `NETWATCH_DISCOVERY_PASSIVE` | `true` | Listen for ARP/DHCP/mDNS/SSDP to build the device inventory (sends nothing) |
| `NETWATCH_DISCOVERY_ACTIVE` | `true` | Initial state of scheduled active scanning (then managed on the Devices page) |
| `NETWATCH_DISCOVERY_INTERFACE` | *(auto)* | Capture interface; default is the default-route interface |
| `NETWATCH_DISCOVERY_SUBNETS` | *(auto)* | Comma-separated subnets to scan; default is the interface's own subnet. Private only, max /22 each |
| `NETWATCH_DISCOVERY_SCAN_INTERVAL_MIN` | `60` | Minutes between scheduled scans (15–10080) |
| `NETWATCH_DISCOVERY_SCAN_RATE_PPS` | `50` | ARP sweep rate, packets per second |
| `NETWATCH_ARP_STORM_PPS` | `100` | ARP rate (10 s average) that opens an ARP-storm event |
| `NETWATCH_DNS_CHECK_INTERVAL_MIN` | `30` | Resolver consistency check (0 disables). Uses your DNS targets' resolvers |
| `NETWATCH_DEVICE_RETENTION_DAYS` | `365` | Forget devices unseen this long (named or monitored devices are kept) |
| `NETWATCH_LOGIN_MAX_FAILURES` | `5` | Failed sign-ins (per client, within 5 min) before a lockout |
| `NETWATCH_LOGIN_LOCKOUT_S` | `300` | Lockout duration |
| `NETWATCH_ICMP_PRIVILEGED` | `true` | Raw-socket ICMP (needs `NET_RAW`) |
| `NETWATCH_MAX_CONCURRENT_PROBES` | `64` | Upper bound on probes in flight |
| `NETWATCH_MAX_TARGETS` | `200` | Maximum number of targets |
| `NETWATCH_SEED_DEFAULTS` | `true` | Create the default targets on first run |
| `TZ` | `America/Toronto` | Container time zone (logs) |

**Probe intervals:** ICMP 1–3600 s, DNS and TCP 5–3600 s, HTTP 10–3600 s. The timeout
defaults to the smaller of the interval and 2 s (ICMP), 3 s (DNS), 5 s (TCP) or 10 s
(HTTP).

**HTTP API** (JSON; every `/api/*` call needs a session cookie except the three auth
endpoints marked public, and state-changing calls need the header `X-Requested-With: netwatch`):

| Endpoint | Purpose |
|---|---|
| `GET /health` | Health: database, scheduler and writer checks (503 when unhealthy). Public |
| `GET /api/auth/status` | Setup required? Signed in? Public |
| `POST /api/auth/setup` | Create the first admin account (only while none exists). Public |
| `POST /api/auth/login`, `POST /api/auth/logout` | Sign in (public) / out |
| `POST /api/auth/password` | Change password (signs out other sessions) |
| `GET /api/updates` | Versions, available releases with notes, policy, agent and progress, history |
| `POST /api/updates/check` | Check GitHub releases now |
| `POST /api/updates/apply` | Install a release: `{"version": "0.2.1"}` |
| `POST /api/updates/rollback` | Undo the last update |
| `PUT /api/updates/policy` | `{"mode": "patch", "window": "03:30"}` |
| `GET /api/updates/log` | Tail of the current/last update log |
| `GET /api/events?start=&end=&kind=&target_id=` | Timeline events (overlapping the range) |
| `GET /api/events/active` | Events still open (ongoing outages, link down) |
| `GET /api/events/{id}` | Event plus evidence: concurrent failures, NIC events, path tests |
| `GET /api/nic`, `GET /api/nic/{iface}/history` | NIC state and counters; per-minute history |
| `GET, POST /api/path-tests` | List, or run mtr now: `{"target_id": 3}` or `{"host": "1.1.1.1"}` |
| `GET, POST /api/iperf/servers`, `PUT, DELETE /api/iperf/servers/{id}` | iperf3 servers |
| `POST /api/iperf/servers/{id}/run`, `GET /api/iperf/tests` | Run download + upload now; results |
| `GET /api/devices?q=` | Device inventory (merged devices grouped), online first |
| `GET, PATCH, DELETE /api/devices/{id}` | Device with IP history, member MACs, target and related events; edit `name`, `type_override`, `notes`; forget |
| `POST /api/devices/{id}/merge`, `POST /api/devices/{id}/unmerge` | `{"into": 7}` treat as the same device; split a MAC off again |
| `POST, DELETE /api/devices/{id}/monitor` | Start / stop monitoring the device (ping target that follows its IP) |
| `GET, PUT /api/discovery` | Capture and scan status, DHCP servers seen; `{"active": true}` |
| `POST /api/discovery/scan`, `POST /api/discovery/dns-check` | Run an active scan / the DNS consistency check now |
| `GET /api/diagnosis?start=&end=` | Network map with live state, diagnosed incidents (newest first) and patterns |
| `GET /api/diagnosis/event/{id}` | The diagnosed incident containing an outage or packet-loss event |
| `GET /api/report?start=&end=&scope=all\|isp&format=json\|md` | Incident report; `md` downloads it as Markdown |
| `GET, PUT /api/admin/settings` | Editable settings with defaults; `{"changes": {"down_after": 4, "raw_retention_days": null}}` (null = default) |
| `POST /api/admin/restart` | Restart NetWatch (applies restart-only settings) |
| `GET /api/admin/sessions`, `DELETE /api/admin/sessions/{id}`, `POST /api/admin/sessions/revoke-others` | Signed-in sessions |
| `GET /api/admin/security-log` | Sign-ins, failures, lockouts and admin actions (30 days) |
| `GET, POST /api/admin/backups`, `GET, DELETE /api/admin/backups/{name}` | List, create, download, delete backups |
| `POST /api/admin/backups/{name}/restore`, `POST /api/admin/restore` | Restore a stored backup, or one uploaded as the raw request body (`.db` or `.db.gz`); applied on restart |
| `GET /api/system` | Version, default route, database size, retention |
| `GET /api/status` | Live state per target (up / degraded / down / no data) |
| `GET, POST /api/targets` | List or create targets |
| `GET, PUT, DELETE /api/targets/{id}` | Read, replace or delete a target (delete removes its history) |
| `POST /api/targets/{id}/test` | Run one probe now (result not stored) |
| `GET /api/targets/{id}/series?start=&end=&points=` | Graph data; raw samples for ranges of 6 h or less, rollups beyond that |
| `GET /api/targets/{id}/export?start=&end=&format=csv\|json` | Raw samples, up to 31 days |
| `GET /metrics` | Prometheus metrics (when enabled) |

## Roadmap
Planned, in order (suggestions welcome):

| Version | Feature |
|---|---|
| Later | **Wi-Fi coverage view:** a floor plan with the Decos placed and each device's signal and roaming per room |
| Later | **Speed tests against your plan:** nightly tests reported as a share of the plan's speed, with a 90-day trend |

## Changelog
See [CHANGELOG.md](CHANGELOG.md).

## License
MIT. See [LICENSE](LICENSE).
