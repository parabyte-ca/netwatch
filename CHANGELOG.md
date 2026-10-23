# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [0.10.1] - 2026-10-23
### Added
- "Laptop" device type: choose it on a device's page, and devices whose name says so
  (MacBook, ThinkPad, Chromebook, ZenBook, IdeaPad, Latitude, EliteBook…) are recognised as
  laptops automatically. Desktop names (iMac, Mac mini, DESKTOP-…) stay "Computer".

## [0.10.0] - 2026-10-23
### Added
- Devices page: sort by any column (device, type, IP, MAC / vendor, last seen) by clicking its
  header; a second click reverses the order. IP addresses sort numerically, "last seen" puts
  online devices first. The chosen order is remembered in the browser.
- Overview: click a target's name on its tile to rename it in place (Enter saves, Esc cancels);
  the rest of the tile still opens its graphs.

### Changed
- Renaming a monitored device's target also renames the device, so the Overview, Targets and
  Devices pages show the same name (renaming the device already renamed the target).

## [0.9.2] - 2026-10-18
### Fixed
- Bufferbloat test failing with "could not load the line": it asked Cloudflare's speed-test
  server for 250 MB per request and streamed uploads without a length, and ignored the answer.
  Requests are now 25 MB downloads and 4 MB uploads with a length, the server's HTTP status is
  checked, and a refusal is reported as such ("speed.cloudflare.com answered HTTP 403…").
  Upload bytes are only counted once the server has accepted them.
- Ask NetWatch could leave a blank screen on phones: the chat scrolled the whole page instead of
  just the conversation. A page that fails to draw now shows the error and a Reload button
  instead of a blank screen, and a problem in the chat panel no longer takes the page down.

## [0.9.1] - 2026-10-18
### Fixed
- Requests that reach NetWatch without a `Content-Length` header (chunked, as some reverse
  proxies and HTTP/2 connections send them) were refused with "length required", which broke
  Install, Roll back and other buttons. They are now accepted, still capped at 256 KB.

## [0.9.0] - 2026-10-18
### Added
- Exposure scan (new **Exposure** page): a nightly (40 minutes after the update window, setting
  "Nightly exposure scan") and on-demand TCP check of about 30 well-known ports on every LAN
  device seen in the last hour, plus the Home Hub's port forwards, UPnP mappings and DMZ host.
  Ranked findings with advice; new "Exposure" timeline events for new open services and new port
  forwards; open services on each device page.
- Network health score (0-100) for the last 24 hours, 7 or 30 days: internet uptime, connection
  quality, latency under load, home network, fibre line and exposure, each with its detail.
  Shown on the Overview and the new **Health** page.
- Weekly health report, written every Monday morning for the previous week (Monday to Sunday),
  with the score and its change, highlights, breakdown, availability, speed tests and incidents;
  kept for two years, downloadable as Markdown, and can be written on demand.
- Ask NetWatch: a chat panel on every page that answers questions about the network with Claude,
  using read-only tools over NetWatch's own data. Off until an Anthropic API key is saved under
  Admin → Ask NetWatch (encrypted like the other credentials); Claude Opus 5.5 by default, with
  Sonnet 5.5 and Haiku 5.5 as cheaper options; 40 questions an hour at most.
- First-run setup now needs a one-time setup code (printed by `setup.sh`, also in
  `data/setup-token` and the container log), so nobody else on the LAN can create the admin
  account first.
- Sessions end after 7 days without use (new setting "Sign out when idle",
  `NETWATCH_SESSION_IDLE_HOURS`, 0 = never).

### Changed
- Opening NetWatch by a host name it doesn't know now shows which name was refused and the exact
  `NETWATCH_ALLOWED_HOSTS` line to add, instead of "Cannot reach the server: unknown host name".
- HTTP probes follow at most 5 redirects; a redirect loop is reported as `too_many_redirects`.

## [0.8.0] - 2026-10-18
### Added
- Live bandwidth per device from the Decos: "top talkers" now, over 24 hours and 7 days on the
  Overview and the map; live traffic on every map node and link (busy links thicker and
  animated); a bandwidth chart and totals on each device page. 1-minute detail for 7 days
  (new setting "Per-device traffic", 1-90 days), then hourly totals.
- Bufferbloat grade (A+ to F): latency to 1.1.1.1 idle versus while downloading and uploading at
  full speed (Cloudflare speed-test endpoints, ~30 s). Nightly 20 minutes after the update window
  (setting "Nightly bufferbloat test") and on demand from Diagnostics, with history. Packet loss
  caused by the test itself never opens an incident; a test that can't load the line is
  recorded as failed instead of graded.
- Home Hub 4000 integration (Admin → Home Hub): reads uptime, fibre receive/transmit levels and
  link state, and the internet (PPP) session every minute, with Test connection and a raw data
  explorer (credentials hidden). New "Home Hub" timeline events: restarts, fibre or internet
  link down, session reconnects, weak fibre signal. New diagnosis causes: Hub restart confirmed
  (94), Bell link down (96), Bell ended the session (88), weak fibre (72). The map's Hub node
  shows model, firmware, uptime and fibre levels; the Bell report gains a "Bell line" section
  (restarts, link-down periods, reconnects, fibre levels, latest bufferbloat grade).
- Roadmap section in the README.

### Fixed
- Deco client speeds were read as bytes per second; the Deco reports kilobytes per second.

## [0.7.2] - 2026-10-18
### Changed
- Diagnosis page: incidents that ended over an hour ago are diagnosed once and remembered;
  loss during an incident is counted in SQL; device lookups and path tests are fetched once per
  request. A 7-day view on a busy database went from ~6.6 s to ~0.8 s (first load) and ~10 ms
  per refresh after that.
- Incident report and long graphs merge the 1-minute rollups in SQL: a 30-day report ~12 s →
  ~1.7 s, a 90-day graph ~1.5 s → ~0.25 s.
- Database: dropped two timestamp-only indexes (about a third of the samples table's size, and a
  second write per probe); purge and rollups walk each target instead. Added indexes for path
  tests by event/target, devices by target/group, and open events (migration runs on start, ~1 s).
- Raw CSV/JSON export streams an hour at a time instead of loading the whole range.
- Graphs update in place instead of being rebuilt on every refresh; polling skips a tick while
  the previous request is still running, and a slow answer for an old time range no longer
  replaces a newer one.

### Fixed
- Samples written late (timeouts, retried flushes) now reach the 1-minute rollups, so long-range
  loss is no longer under-counted.
- The map's incident replay includes failures merged into the incident later.
- The Bell report's availability only counts downtime inside the report period.
- Live status could fail intermittently while new probe results arrived.

### Security
- Discovery is protected against floods of made-up hardware addresses: at most 30 new devices are
  recorded per minute (one "MAC flood" alert instead), in-memory state is bounded and pruned,
  alerts are capped per type, and the gateway-MAC alert is per gateway.
- A monitored device's ping target only follows it to addresses on the local network.
- One malformed packet or Deco record no longer discards the rest of the batch.
- The DHCP probe reuses one made-up client address instead of a new one every scan; reverse DNS
  lookups run in parallel with a short timeout; full-size frames are captured (large mDNS answers).

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
