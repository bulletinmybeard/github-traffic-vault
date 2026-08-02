"""Discover owned repos and upsert into `repos` table."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from github_traffic_vault.github_api import GitHubClient
from github_traffic_vault.models import DailyStars, Repo

log = logging.getLogger(__name__)


@dataclass
class DiscoveredRepo:
    repo: Repo
    is_new: bool


def discover_and_upsert(
    session: Session,
    gh: GitHubClient,
    exclude_repos: frozenset[str] | None = None,
    include_private: bool = False,
    now: datetime | None = None,
) -> list[DiscoveredRepo]:
    now = now or datetime.now(UTC)
    payloads = gh.list_owned_repos(include_private=include_private)
    if exclude_repos:
        before = len(payloads)
        payloads = [p for p in payloads if p.get("name", "").lower() not in exclude_repos]
        skipped = before - len(payloads)
        if skipped:
            log.info("Excluding %d repo(s) via config", skipped)
    results: list[DiscoveredRepo] = []
    for payload in payloads:
        result = _upsert_one(session, payload, now)
        results.append(result)
    session.flush()
    log.debug("discovered %d repos (%d new)", len(results), sum(1 for r in results if r.is_new))
    return results


def _upsert_one(session: Session, payload: dict[str, Any], now: datetime) -> DiscoveredRepo:
    full_name = payload["full_name"]
    existing = session.scalar(select(Repo).where(Repo.full_name == full_name))

    owner = payload["owner"]["login"]
    name = payload["name"]
    is_fork = bool(payload.get("fork", False))
    is_archived = bool(payload.get("archived", False))
    is_private = bool(payload.get("private", False))
    stargazers = int(payload.get("stargazers_count", 0))
    forks = int(payload.get("forks_count", 0))
    watchers = int(payload.get("watchers_count", 0))
    default_branch = payload.get("default_branch")
    created_at = _parse_iso(payload.get("created_at"))

    if existing is None:
        repo = Repo(
            owner=owner,
            name=name,
            full_name=full_name,
            is_fork=is_fork,
            is_archived=is_archived,
            is_private=is_private,
            stargazers=stargazers,
            forks=forks,
            watchers=watchers,
            default_branch=default_branch,
            created_at=created_at,
            first_seen_at=now,
            last_seen_at=now,
        )
        session.add(repo)
        session.flush()
        _record_star_snapshot(session, repo.id, now.date(), stargazers)
        return DiscoveredRepo(repo=repo, is_new=True)

    existing.owner = owner
    existing.name = name
    existing.is_fork = is_fork
    existing.is_archived = is_archived
    existing.is_private = is_private
    existing.stargazers = stargazers
    existing.forks = forks
    existing.watchers = watchers
    existing.default_branch = default_branch
    if created_at is not None:
        existing.created_at = created_at
    existing.last_seen_at = now
    _record_star_snapshot(session, existing.id, now.date(), stargazers)
    return DiscoveredRepo(repo=existing, is_new=False)


def _record_star_snapshot(session: Session, repo_id: int, on: date, count: int) -> None:
    """Upsert today's star count for period-end historical display."""
    row = session.get(DailyStars, (repo_id, on))
    if row is None:
        session.add(DailyStars(repo_id=repo_id, date=on, count=count))
    else:
        row.count = count


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
