# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.6.0] - 2026-07-09

### Added

- Helper functions to format sync run integers for the UI, abbreviating from 1000 upward (e.g., 1234 > '1.2k')
- Settings at `/settings` (General, Cards, Sync) with gear button that reads and writes `config.yaml` on save
- Optional private repo discovery (`sync.include_private` in `config.yaml` or settings toggle)
- Index sort and filter toolbar, period-over-period deltas on tiles, view/clone sparklines, and fuzzy repo search
- Detail page: top referrers, top paths, and collapsible GitHub data revision log
- `config.example.yaml` template with `auth`, `display`, `cards`, `sync`, `paths`, and `server` sections
- Global CLI flag `--config` / `-c` for a non-default config file path

### Changed

- **Breaking:** all configuration is in `config.yaml` only. Secrets (`auth.github_token`, `auth.secret_key`) live in YAML, not `.env`. Token fallback remains `gh auth token` when `auth.github_token` is empty.

### Removed

- `.env` / `.env.example` and all `GITHUB_TRAFFIC_VAULT_*` environment variables
- Legacy `.env` auto-import and `envfile.py`

## [0.5.0] - 2026-07-05

### Added

- Vault archive date range inline next to the period picker on index and detail pages, e.g., `(2026-05-24 - 2026-07-04)` that runs through **today** in the display timezone
- `/api/repos.json` adds an `archive` object (`earliest`, `latest`, `through`, `range_label`)
- Close the period picker on Escape or an outside click (`period-menu.js`)

### Fixed

- Period boundaries use the `GITHUB_TRAFFIC_VAULT_DISPLAY_TZ` calendar date (`today_in_tz`) instead of UTC midnight
- Tile **TODAY** row uses GitHub's UTC traffic day bucket (`traffic_today_utc`) and loads via a dedicated query, independent of the selected period window
- **All time** totals include every archived row (`open_end` - no upper date cap at today)
- Sync parses GitHub traffic timestamps as explicit UTC calendar days

## [0.4.0] - 2026-06-25

### Added

- Index page period selector with the same presets as the detail page: This month, Last 7/14 days, Last month, Last 2/3 months, All time, and a custom from/to range
- `range=month` query param for calendar month-to-date on index, detail, and `/api/repos.json`
- Home page title links back to `/`

### Changed

- Default traffic window on the index and detail pages is now **Last month** (rolling 30 days) instead of 14 days
- `/api/repos.json` returns a `period` object (`start`, `end`, `kind`, `days`, `label`) instead of a top-level `days` field; the same query params still apply

## [0.3.0] - 2026-06-08

### Added

- Add index, detail, and date-range picker screenshots to the README, plus Poetry, Ruff, and mypy badges

### Changed

- Simplify `Release` GitHub workflow: drop the build/artifact/checksum steps; the workflow now only creates a GitHub release with notes extracted from the matching `CHANGELOG.md` section
- CI workflow: add `poetry check --lock`, venv caching via `actions/cache`, and `--sync` on `poetry install`

### Fixed

- Index tile grid pushed wide tiles past the right edge of the viewport on repos with large view/clone counts and long repository names. The grid now uses `minmax(0, 1fr)` so tiles stay in-row

## [0.2.0] - 2026-06-08

### Added

- Add an option to exclude public repositories like profile or deprecated repositories from the sync via env var (comma-separated list of repository names)
- Render timestamps in the web UI and CLI in a configurable display timezone via `GITHUB_TRAFFIC_VAULT_DISPLAY_TZ` (IANA name, defaults to UTC); storage and machine-readable output (JSON API, exports) stay UTC
- Add `Release` GitHub workflow: on a `v*` tag (or manual dispatch) it builds the wheel + sdist, verifies the tag matches the package version, and creates a GitHub release with checksums and changelog notes

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
