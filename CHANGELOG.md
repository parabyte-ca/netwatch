# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [0.7.1] - 2026-10-18
### Security
- Upgrade agent and `update.sh` no longer trust anything the container can write: the
  rollback record, lock and logs moved to a root-only `.state/` folder; status files to
  `upgrade-status/`, mounted read-only; requests are opened without following symlinks; file
  work in `data/` runs in a throwaway container; rollback records are strictly validated;
  backup pruning can no longer be tricked into deleting other files; downgrades are refused.
  This closes a path from a compromised container to root on the TrueNAS host. Existing
  installs migrate automatically on the agent's next run (no manual step).
- Release images are signed in CI (Sigstore, keyless) and `update.sh` verifies the signature
  and that the release tag matches the image's source commit before deploying.
  `NETWATCH_VERIFY_IMAGES=false` switches this off.
- Deco: the stored owner password is only ever sent to the saved Deco address; names must
  resolve to LAN addresses; redirects to other hosts are refused; the Deco's sign-in key is
  pinned on first use and a changed key stops polling with a security event (re-trust it with
  Test connection); polling stops after 3 failed sign-ins (protects the owner account from
  lockout); session tokens are scrubbed from errors; Deco-supplied names are cleaned.
- Sign-in: attempts are counted when they start (parallel requests can't bypass the lockout),
  IPv6 clients are throttled per /64, password checks are limited to two at a time, the
  password-change form is throttled too, and repeated refusals are logged once per lockout.
- Requests for host names other than an IP address, `localhost` or `NETWATCH_ALLOWED_HOSTS`
  are refused (DNS rebinding); request bodies are capped at 256 KiB (except backup uploads).
- Restores: backups containing triggers or views are rejected, a compressed upload can't
  expand past the size limit or the free space, concurrent restores can't collide, and all
  sessions are signed out after a restore.
- `/metrics` always requires sign-in or the token (also when switched off without a restart);
  the API schema (`/openapi.json`) is no longer served; `/health` no longer returns error text.
- CI: actions pinned to commit SHAs, `VERSION` validated, checkout credentials not persisted.

### Fixed
- Restoring a stored backup no longer reads the whole file on the event loop.
- A failed rollback step no longer leaves NetWatch stopped; a rollback first saves the current
  database as an extra backup.

## [0.7.0] - 2026-10-17
### Added
- Network map page: interactive, automatically laid out map (Internet, Bell, Home Hub, switch,
  TrueNAS, Decos, key devices and per-Deco groups of the rest) with link latency and loss,
  wired/Wi-Fi/logical links, a details panel, and drag-to-arrange with saved positions and reset.
- Incident replay on the map: time slider and incident picker, with the likely cause and failing
  nodes highlighted; "Show on map" links from Diagnosis and event pages.
- Deco mesh integration (Admin → Deco mesh): reads the Decos' local API (owner account; use a
  manager account in the Deco app) for units, backhaul and which device is on which Deco/band.
  The password is encrypted with a key file (`data/.secret_key`) kept outside the database.
- Wi-Fi events on the timeline: Deco offline/online, backhaul changes, roaming and Wi-Fi
  disconnects longer than a minute for monitored devices.
- Diagnosis causes from the Deco data: a Deco went offline, all failing devices on one Deco,
  a monitored device dropped off Wi-Fi or roamed.
- Device pages show the Deco, band and Deco app name.

## [0.6.0] - 2026-10-17
### Added
- Admin page: all tunable settings editable in the web UI with defaults and reset; values
  override `.env`, apply immediately where possible, and a restart banner covers the rest.
- Restart NetWatch from the web UI (Docker's restart policy brings it back).
- Signed-in sessions list with sign-out of one or all other sessions.
- Configurable sign-in lockout (`NETWATCH_LOGIN_MAX_FAILURES`, `NETWATCH_LOGIN_LOCKOUT_S`).
- Security log: sign-ins, failures, lockouts, password and settings changes, backups,
  restores and restarts (also on the Timeline as "Security", hidden by default).
- Database backup and restore from the web page: create, download, delete, restore a stored or
  uploaded (`.db`/`.db.gz`) backup; validated, then applied on restart after a safety copy.

### Changed
- `/metrics` is always routed and answers 404 while disabled, so it can be switched on from
  the UI.

## [0.5.0] - 2026-10-17
### Added
- Diagnosis engine: overlapping failures are grouped into incidents and each is explained by
  ranked, explainable causes with confidence, evidence and next steps, using what failed versus
  what kept working along the network map (TrueNAS link, switch, Home Hub, Bell, internet,
  Decos, devices), NIC events, network anomalies and path tests.
- Pattern detection: failures repeating at a fixed interval or clustering at a time of day.
- Diagnosis page with a live network map (the likely cause's layer highlighted), incidents and
  patterns; the most likely cause on every outage and packet-loss event page.
- Incident report for Bell: printable (save as PDF from the browser) or Markdown download, with
  incidents, evidence, path tests, what was ruled out, per-target measurements and speed tests;
  optionally limited to incidents pointing at Bell.

## [0.4.1] - 2026-10-17
### Changed
- Active scanning is now on by default (hourly). Switch it off on the Devices page; existing
  installs that never touched the toggle pick up the new default.
- Dependabot now proposes major-version updates for GitHub Actions, grouped into one PR (still
  ignored for application dependencies and base images).

## [0.4.0] - 2026-10-17
### Added
- Passive device discovery from ARP, DHCP, mDNS and SSDP broadcasts and the host ARP cache,
  with an in-kernel packet filter so only those packets reach NetWatch.
- Opt-in active scanning: ARP sweep of the local private subnet (rate-limited, max /22), a
  DHCP server probe, SSDP and mDNS queries and reverse DNS via the gateway; scheduled or on
  demand.
- Device inventory: vendor (offline IEEE registry), names, model, advertised services,
  explainable type guess, IP history, online/last seen; merging of randomised MACs by
  hostname plus manual merge/split; rename, type override, notes; one-click monitoring with a
  ping target that follows the device's IP.
- Anomaly events: duplicate IP, more than one DHCP server, ARP storm, gateway MAC change,
  repeated DHCP rejoins, frequent IP changes, double NAT and carrier-grade NAT (from path
  tests), resolver disagreement and NXDOMAIN rewriting. "New device" events after a one-hour
  learning period.
- Devices page and device detail page; anomaly and device events on the Timeline, with an
  evidence section and a hint about the usual cause.

### Changed
- The CI image smoke test also checks raw packet capture with the kernel filter and the
  vendor registry inside the hardened container.

## [0.3.0] - 2026-10-17
### Added
- Outage detection with hysteresis (3 failures to declare down, 3 successes to recover) and a
  separate packet-loss state for intermittent loss; outage times run from first failure to
  first success.
- Event timeline (outages, packet loss, NIC link/speed/errors, NetWatch start/stop) with an
  event detail page: latency around the event, concurrent failures, NIC events, path tests.
- Active incidents and last-24-hours events on the Overview; outage and loss periods shaded
  on latency graphs.
- TrueNAS NIC telemetry from sysfs: link down/up, sub-poll flaps via carrier changes,
  speed/duplex renegotiation, error and CRC counters, per-minute counter history; watches
  the physical ports beneath a bridge, bond or VLAN.
- Path tests with mtr: scheduled for public targets, automatic when an outage starts, and on
  demand; reports the first hop of persistent loss (ignoring silent hops) and route changes.
- Throughput tests with iperf3 (LAN and WAN servers, download and upload, manual or
  scheduled).
- Diagnostics page (NIC status, path tests, throughput servers and results).

### Changed
- The container image now includes `mtr-tiny` and `iperf3`.
- `/health` also checks the incident recorder and NIC monitor.

## [0.2.0] - 2026-10-17
### Added
- Sign-in for everything: first-run admin setup, scrypt password hashes, server-side
  sessions in HttpOnly SameSite=Strict cookies, login lockout after 5 failures, CSRF header
  check, password change, and `python -m netwatch.reset_password` for recovery.
- In-app updates: System page with running/latest version, release notes, Install button,
  live update log, update history and Roll back.
- Automatic updates with a policy (off, notify, patch, minor) and a nightly maintenance window;
  bug-fix releases install automatically by default, larger releases wait for a click. Failed
  automatic updates are rolled back and not retried.
- Host-side upgrade agent (`scripts/upgrade-agent.sh`), installed by `setup.sh` as a TrueNAS
  cron job through the middleware so it survives TrueNAS upgrades.
- CI publishes a GitHub Release (notes from this changelog) for every version tag, after the
  image is pushed; the update check reads those releases.
- Optional bearer token for Prometheus scraping of `/metrics`.

### Changed
- `docker-compose.yml` mounts `./upgrade` for the upgrade agent.
- Releases are automatic: merging a PR that bumps `VERSION` makes CI publish the versioned
  image, the git tag and the GitHub Release (no manual tagging).
- Dependabot ignores major-version bumps (and Python/Node base-image minor bumps), which are
  planned deliberately instead.

## [0.1.0] - 2026-10-17
### Added
- Phase 1 scaffold: FastAPI (Python 3.12) backend, React + Vite + TypeScript frontend,
  single Docker image published to GHCR.
- Probe engine: async scheduler with per-target intervals (ICMP from 1 s), ICMP, DNS, TCP
  connect and HTTP probes, concurrency limit and jittered start.
- Storage: SQLite (WAL) with raw samples (7-day retention), 1-minute rollups (90-day
  retention), hourly purge and incremental vacuum, Alembic migrations.
- First-run defaults: auto-detected default gateway, 1.1.1.1, 8.8.8.8, and DNS resolver
  comparison (gateway vs Cloudflare vs Google).
- Dashboard: health summary, grouped live status tiles, SmokePing-style latency graphs
  (median, p5–p95 / p25–p75 smoke, loss bars) from 15 minutes to 90 days, run-test-now,
  CSV/JSON export, target management.
- Operations: `/health` endpoint and container health check, structured JSON logs, optional
  Prometheus `/metrics`, online database backup CLI.
- Deployment: host networking with only `NET_RAW` and `NET_ADMIN`, read-only root
  filesystem, `setup.sh`, and `update.sh` with pre-update database backup, health check
  and automatic rollback (`--rollback` for manual undo).
- CI: lint, tests, shellcheck and image build on every push; image publish on `main` and
  version tags.
