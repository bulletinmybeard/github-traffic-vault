# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
