# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Add an option to exclude public repositories like profile or deprecated repositories from the sync via env var (comma-separated list of repository names)
- Render timestamps in the web UI and CLI in a configurable display timezone via `GITHUB_TRAFFIC_VAULT_DISPLAY_TZ` (IANA name, defaults to UTC); storage and machine-readable output (JSON API, exports) stay UTC

### Changed

- Outsource the `tiles per row` number from the StyleSheet to an env var which defaults to 5 and caps on 5. Every number above 5 will fall back to 5

### Fixed

- Dev `docker-compose.yml` forwarded only a fixed subset of env vars; switched to `env_file: .env` so all `GITHUB_TRAFFIC_VAULT_*` config (display tz, exclude repos, tiles per row) reaches the container

## [0.1.0] - 2026-06-07

First release. Permanently archives GitHub traffic for public owned repos,
keeping the full history GitHub drops after ~14 days.

### Added

- SQLite archive of per-repo traffic: views, clones, top referrers, top paths
- CLI (`github-traffic-vault`): `sync`, `repos`, `show`, `export`, `top`, `serve`
- FastAPI + Jinja2 web UI: per-repo overview cards and detail pages with a SVG traffic chart
- Per-repo open PR count, latest release/tag, and most recent workflow run
- `change_events` tracking to infer GitHub's traffic refresh cadence
- ETag conditional requests to cut redundant API calls
- Docker setup for local dev with hot-reload
- Docker image built from the pinned `poetry.lock` (two-stage build)
- Configurable session-cookie `Secure` flag (`GITHUB_TRAFFIC_VAULT_SECURE_COOKIE`) and trusted proxy IPs (`GITHUB_TRAFFIC_VAULT_FORWARDED_IPS`)

### Security

- CSRF protection on the web UI's sync action (signed session + per-session token)
- Chart data rendered as escaped JSON, so repo content can't inject script
- Repo-detail 404 returns plain text, so `owner`/`repo` path segments can't inject HTML
- Reject protocol-relative (`//host`) and backslash (`/\host`) `next` values on `/sync`
