# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

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
