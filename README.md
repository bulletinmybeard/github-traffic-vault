# GitHub Traffic Vault

[![CI](https://github.com/bulletinmybeard/github-traffic-vault/actions/workflows/ci.yml/badge.svg)](https://github.com/bulletinmybeard/github-traffic-vault/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)

GitHub only retains repository traffic data (views, clones, top referrers, top paths) for about 14 days. **GitHub Traffic Vault** permanently archives that data in SQLite for all of your public repos, so nothing ever gets lost.

<table>
  <tr valign="top">
    <td width="40%">
      <a href=".github/assets/github-traffic-vault-index-demo.png">
        <img width="100%" alt="Index page: every public repo as a tile with 14-day and today view/clone totals" src=".github/assets/github-traffic-vault-index-demo.png">
      </a>
    </td>
    <td width="40%">
      <a href=".github/assets/github-traffic-vault-detail-demo.png">
        <img width="100%" alt="Detail page: per-repo daily views/clones chart with CI, release, and open-PR status" src=".github/assets/github-traffic-vault-detail-demo.png">
      </a>
    </td>
    <td width="20%">
      <a href=".github/assets/github-traffic-vault-date-picker.png">
        <img width="100%" alt="Date-range picker popover: preset windows (7/14/30 days, all time) plus a custom from/to range" src=".github/assets/github-traffic-vault-date-picker.png">
      </a>
    </td>
  </tr>
  <tr>
    <td align="center"><em>Index page: showing all synced public repositories</em></td>
    <td align="center"><em>Detail page: per-repo history and status</em></td>
    <td align="center"><em>Date-range picker</em></td>
  </tr>
</table>

## What it does

- Discovers all public owned repos via the GitHub REST API
- Fetches the four traffic endpoints per public repo
- Upserts daily series into SQLite and emits one row in `change_events` for every cell that changed since the previous sync
- Tracks ETags per (repo, endpoint) so re-syncs cost nothing when nothing changed
- Comes with a small FastAPI web UI for browsing the stored data with a one-click `Sync Now` button

## Setup

Requires **Python 3.12** and **Poetry**. You need a GitHub Personal
Access Token with `repo` scope. Two ways to provide it:

- Set `GITHUB_TOKEN` in `.env` (works everywhere: Mac, Linux, CI). See `.env.example` for the template
- On the Mac, leave `.env` empty and `github-traffic-vault` will fall back to `gh auth token` (requires the [GitHub CLI](https://cli.github.com/), logged in via `gh auth login`)

The `gh` CLI is **only** used to fetch the token. All actual data fetching goes through the GitHub REST API directly via `httpx`.

```bash
poetry install
poetry run github-traffic-vault sync
```

## CLI

```bash
github-traffic-vault sync                          # discover + sync (cron entry point)
github-traffic-vault sync --only owner/repo        # restrict to specific repos
github-traffic-vault sync --dry-run                # hit the API, roll back DB

github-traffic-vault repos                         # list known repos
github-traffic-vault show <repo> [--since DATE]    # daily table + latest top lists

github-traffic-vault export <repo|all> [--format csv|json] [--kind views|clones|referrers|paths]
github-traffic-vault top [--by views|clones] [--since DATE] [--limit N]

github-traffic-vault serve [--host 127.0.0.1] [--port 8800] [--reload]
```

## Web UI

```bash
poetry run github-traffic-vault serve
# open http://127.0.0.1:8800
```

The SPA, server-rendered (FastAPI + Jinja2). Each repo gets a card
showing 14-day totals and a per-day breakdown grouped by month.
The `Sync Now` button kicks off a real sync (blocks ~7s, then re-renders).
The same data is available as JSON at `/api/repos.json`.

With `--reload`, you enable `uvicorn` auto-reload for dev (or run via Docker with hot-reload, see below).

## Docker

A `Dockerfile` and a compose file for local dev with hot-reload.

### Mac dev (hot-reload)

```bash
cp .env.example .env
# fill GITHUB_TOKEN, or leave empty to let the container fall back to
# `gh auth token` (gh is installed inside the image)

docker compose up --build
# UI at http://127.0.0.1:8800; source bind-mounted, uvicorn --reload
```

Manual sync from another terminal:

```bash
docker compose exec github-traffic-vault github-traffic-vault sync
```

## Storage

```
data/
|-- github-traffic-vault.db    # SQLite (WAL mode)
`-- github-traffic-vault.log   # rotating log
```

Inspect with `sqlite3 data/github-traffic-vault.db` or another compatible SQLite client.

## Schema

SQLite (WAL mode), nine tables: repos, the daily views/clones series, top
referrers and paths, releases/tags, open PR counts, an `etags` table backing
conditional requests, and a `change_events` table recording every value that
changed between syncs (used to infer GitHub's refresh cadence).

## Limitations

- Single GitHub account
- Public repos only (private repo traffic is meaningless and skipped at the API layer)
- No tests in this iteration
- ETag support on traffic endpoints isn't documented by GitHub. The `etags` table is harmless if a server ever stops honoring them
- The web UI binds to `127.0.0.1` only with no auth. To reach it from another machine, tunnel via SSH (`ssh -L 8800:127.0.0.1:8800 host`) or put it behind a reverse proxy

## License

MIT, see [LICENSE](LICENSE).
