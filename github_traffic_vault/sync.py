"""Per-run / per-repo sync logic with diff tracking."""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from datetime import date as _date
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from github_traffic_vault.config import Config
from github_traffic_vault.github_api import FetchResult, GitHubClient, GitHubError
from github_traffic_vault.models import (
    ChangeEvent,
    DailyClones,
    DailyViews,
    Etag,
    PathSnapshot,
    ReferrerSnapshot,
    Repo,
    RepoSync,
    SyncRun,
)

log = logging.getLogger(__name__)


@dataclass
class SyncOptions:
    only_repos: list[str] | None = None
    dry_run: bool = False
    exclude_repos: frozenset[str] | None = None


def run_sync(
    session: Session,
    gh: GitHubClient,
    cfg: Config,
    opts: SyncOptions,
    repos: list[Repo],
    on_progress: Callable[[Repo, int, int], None] | None = None,
) -> SyncRun:
    """Run a sync over ``repos``."""
    now = datetime.now(UTC)
    run = SyncRun(started_at=now, repos_total=0, repos_ok=0, repos_err=0)
    session.add(run)
    session.flush()

    targets = _filter_repos(repos, opts.only_repos, exclude=opts.exclude_repos)
    run.repos_total = len(targets)
    session.flush()

    total = len(targets)
    for index, repo in enumerate(targets, start=1):
        if on_progress is not None:
            on_progress(repo, index, total)
        rs = RepoSync(sync_run_id=run.id, repo_id=repo.id, started_at=datetime.now(UTC))
        session.add(rs)
        session.flush()
        try:
            _sync_one_repo(session, gh, repo, run, rs)
            rs.finished_at = datetime.now(UTC)
            repo.last_synced_at = rs.finished_at
            if rs.views_changed_days or rs.clones_changed_days or rs.referrers_changed or rs.paths_changed:
                repo.last_traffic_change_at = rs.finished_at
            run.repos_ok += 1
        except GitHubError as exc:
            rs.error = str(exc)
            rs.finished_at = datetime.now(UTC)
            run.repos_err += 1
            log.warning("repo=%s error=%s", repo.full_name, exc)

        session.flush()

        if gh.rate_remaining is not None and gh.rate_remaining < cfg.rate_limit_floor:
            run.notes = f"early abort: rate_remaining={gh.rate_remaining} < floor={cfg.rate_limit_floor}"
            log.warning(run.notes)
            break

    run.finished_at = datetime.now(UTC)
    run.rate_remaining = gh.rate_remaining
    return run


def _filter_repos(
    repos: list[Repo], only: list[str] | None, exclude: frozenset[str] | None = None
) -> list[Repo]:
    if only:
        wanted = set(only)
        repos = [r for r in repos if r.full_name in wanted or r.name in wanted]
    if exclude:
        repos = [r for r in repos if r.name.lower() not in exclude]
    return repos


def _get_etag(session: Session, repo_id: int, endpoint: str) -> str | None:
    row = session.get(Etag, (repo_id, endpoint))
    return row.etag if row else None


def _save_etag(session: Session, repo_id: int, endpoint: str, etag: str | None) -> None:
    if etag is None:
        return
    row = session.get(Etag, (repo_id, endpoint))
    now = datetime.now(UTC)
    if row is None:
        session.add(Etag(repo_id=repo_id, endpoint=endpoint, etag=etag, last_fetched_at=now))
    else:
        row.etag = etag
        row.last_fetched_at = now


def _sync_one_repo(session: Session, gh: GitHubClient, repo: Repo, run: SyncRun, rs: RepoSync) -> None:
    # views
    views_res = gh.fetch_views(repo.owner, repo.name, etag=_get_etag(session, repo.id, "views"))
    if views_res.not_modified:
        rs.http_304s += 1
    else:
        rs.http_calls += 1
        rs.views_changed_days = _sync_daily(session, views_res, repo, run, "views")
        _save_etag(session, repo.id, "views", views_res.etag)

    # clones
    clones_res = gh.fetch_clones(repo.owner, repo.name, etag=_get_etag(session, repo.id, "clones"))
    if clones_res.not_modified:
        rs.http_304s += 1
    else:
        rs.http_calls += 1
        rs.clones_changed_days = _sync_daily(session, clones_res, repo, run, "clones")
        _save_etag(session, repo.id, "clones", clones_res.etag)

    # referrers
    ref_res = gh.fetch_referrers(repo.owner, repo.name, etag=_get_etag(session, repo.id, "referrers"))
    if ref_res.not_modified:
        rs.http_304s += 1
    else:
        rs.http_calls += 1
        rs.referrers_changed = _sync_top_list(
            session, ref_res, repo, run, ReferrerSnapshot, key_fields=("referrer",)
        )
        _save_etag(session, repo.id, "referrers", ref_res.etag)

    # paths
    path_res = gh.fetch_paths(repo.owner, repo.name, etag=_get_etag(session, repo.id, "paths"))
    if path_res.not_modified:
        rs.http_304s += 1
    else:
        rs.http_calls += 1
        rs.paths_changed = _sync_top_list(
            session, path_res, repo, run, PathSnapshot, key_fields=("path", "title")
        )
        _save_etag(session, repo.id, "paths", path_res.etag)

    _sync_repo_status(session, gh, repo, rs)


def _sync_daily(session: Session, res: FetchResult, repo: Repo, run: SyncRun, kind: str) -> int:
    if res.not_modified or res.payload is None:
        return 0

    entries = res.payload.get("views" if kind == "views" else "clones", [])
    model: type[DailyViews] | type[DailyClones] = DailyViews if kind == "views" else DailyClones

    changed = 0
    for entry in entries:
        d = _parse_date(entry["timestamp"])
        new_count = int(entry["count"])
        new_uniques = int(entry["uniques"])
        existing = cast(DailyViews | DailyClones | None, session.get(model, (repo.id, d)))
        now = datetime.now(UTC)

        if existing is None:
            session.add(
                model(
                    repo_id=repo.id,
                    date=d,
                    count=new_count,
                    uniques=new_uniques,
                    first_seen_at=now,
                    last_changed_at=now,
                    change_count=1,
                )
            )
            session.add(
                ChangeEvent(
                    sync_run_id=run.id,
                    repo_id=repo.id,
                    kind=kind,
                    date=d,
                    prev_count=None,
                    new_count=new_count,
                    count_delta=new_count,
                    prev_uniques=None,
                    new_uniques=new_uniques,
                    uniques_delta=new_uniques,
                    recorded_at=run.started_at,
                )
            )
            changed += 1
            continue

        if existing.count == new_count and existing.uniques == new_uniques:
            continue

        session.add(
            ChangeEvent(
                sync_run_id=run.id,
                repo_id=repo.id,
                kind=kind,
                date=d,
                prev_count=existing.count,
                new_count=new_count,
                count_delta=new_count - existing.count,
                prev_uniques=existing.uniques,
                new_uniques=new_uniques,
                uniques_delta=new_uniques - existing.uniques,
                recorded_at=run.started_at,
            )
        )
        existing.count = new_count
        existing.uniques = new_uniques
        existing.last_changed_at = now
        existing.change_count += 1
        changed += 1

    return changed


def _sync_top_list(
    session: Session,
    res: FetchResult,
    repo: Repo,
    run: SyncRun,
    model: type[ReferrerSnapshot] | type[PathSnapshot],
    key_fields: tuple[str, ...],
) -> bool:
    if res.not_modified or res.payload is None:
        return False

    rows: list[dict[str, Any]] = list(res.payload)
    new_hash = _snapshot_hash(rows, key_fields)

    prev_run_id = session.scalar(
        select(model.sync_run_id).where(model.repo_id == repo.id).order_by(model.sync_run_id.desc()).limit(1)
    )

    if prev_run_id is not None:
        prev_rows = cast(
            list[ReferrerSnapshot | PathSnapshot],
            list(
                session.scalars(
                    select(model).where(model.repo_id == repo.id, model.sync_run_id == prev_run_id)
                ).all()
            ),
        )
        prev_payload = [_row_to_dict(r, key_fields) for r in prev_rows]
        prev_hash = _snapshot_hash(prev_payload, key_fields)
        if prev_hash == new_hash:
            return False

    for entry in rows:
        if model is ReferrerSnapshot:
            session.add(
                ReferrerSnapshot(
                    sync_run_id=run.id,
                    repo_id=repo.id,
                    referrer=entry["referrer"],
                    count=int(entry["count"]),
                    uniques=int(entry["uniques"]),
                )
            )
        else:
            session.add(
                PathSnapshot(
                    sync_run_id=run.id,
                    repo_id=repo.id,
                    path=entry["path"],
                    title=entry.get("title"),
                    count=int(entry["count"]),
                    uniques=int(entry["uniques"]),
                )
            )
    return True


def _snapshot_hash(rows: list[dict[str, Any]], key_fields: tuple[str, ...]) -> str:
    normalized = sorted(
        (
            *(r.get(k) for k in key_fields),
            int(r.get("count", 0)),
            int(r.get("uniques", 0)),
        )
        for r in rows
    )
    blob = json.dumps(normalized, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _row_to_dict(row: ReferrerSnapshot | PathSnapshot, key_fields: tuple[str, ...]) -> dict[str, Any]:
    out: dict[str, Any] = {"count": row.count, "uniques": row.uniques}
    for k in key_fields:
        out[k] = getattr(row, k)
    return out


def _parse_date(ts: str) -> _date:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).date()


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _sync_repo_status(session: Session, gh: GitHubClient, repo: Repo, rs: RepoSync) -> None:
    """Best-effort CI / release / open-PR snapshot."""
    _sync_ci(session, gh, repo, rs)
    _sync_release(session, gh, repo, rs)
    _sync_open_prs(session, gh, repo, rs)


def _sync_ci(session: Session, gh: GitHubClient, repo: Repo, rs: RepoSync) -> None:
    etag = _get_etag(session, repo.id, "actions_runs")
    try:
        res = gh.fetch_latest_run(repo.owner, repo.name, etag=etag)
    except GitHubError as exc:
        log.warning("repo=%s ci fetch failed: %s", repo.full_name, exc)
        return
    if res.not_modified:
        rs.http_304s += 1
        return
    rs.http_calls += 1
    runs = (res.payload or {}).get("workflow_runs", [])
    if runs:
        latest = runs[0]
        repo.ci_status = latest.get("status")
        repo.ci_conclusion = latest.get("conclusion")
        repo.ci_workflow = latest.get("name")
        repo.ci_branch = latest.get("head_branch")
        repo.ci_run_url = latest.get("html_url")
        repo.ci_run_at = _parse_dt(latest.get("updated_at"))
    _save_etag(session, repo.id, "actions_runs", res.etag)


def _sync_release(session: Session, gh: GitHubClient, repo: Repo, rs: RepoSync) -> None:
    etag = _get_etag(session, repo.id, "release")
    try:
        res = gh.fetch_latest_release(repo.owner, repo.name, etag=etag)
    except GitHubError as exc:
        log.warning("repo=%s release fetch failed: %s", repo.full_name, exc)
        return
    if res.not_modified:
        rs.http_304s += 1
        return
    rs.http_calls += 1
    if res.status != 404:
        rel = res.payload or {}
        repo.release_kind = "release"
        repo.release_name = rel.get("tag_name")
        repo.release_url = rel.get("html_url")
        repo.release_at = _parse_dt(rel.get("published_at"))
        _save_etag(session, repo.id, "release", res.etag)
        return
    # no published Release (fall back to the newest git tag)
    tag_etag = _get_etag(session, repo.id, "tag")
    try:
        tag_res = gh.fetch_latest_tag(repo.owner, repo.name, etag=tag_etag)
    except GitHubError as exc:
        log.warning("repo=%s tag fetch failed: %s", repo.full_name, exc)
        return
    if tag_res.not_modified:
        rs.http_304s += 1
        return
    rs.http_calls += 1
    tags = tag_res.payload or []
    if tags:
        name = tags[0].get("name")
        repo.release_kind = "tag"
        repo.release_name = name
        repo.release_url = f"https://github.com/{repo.full_name}/releases/tag/{name}"
        repo.release_at = None
    else:
        repo.release_kind = None
        repo.release_name = None
        repo.release_url = None
        repo.release_at = None
    _save_etag(session, repo.id, "tag", tag_res.etag)


def _sync_open_prs(session: Session, gh: GitHubClient, repo: Repo, rs: RepoSync) -> None:
    # GraphQL, not REST: GET /pulls 404s on repos created from ~2026 on.
    # No conditional-request support here, so no ETag/304 bookkeeping.
    try:
        repo.open_pr_count = gh.fetch_open_pr_count(repo.owner, repo.name)
    except GitHubError as exc:
        log.warning("repo=%s pulls fetch failed: %s", repo.full_name, exc)
        return
    rs.http_calls += 1
