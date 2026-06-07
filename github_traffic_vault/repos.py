"""Discover owned repos and upsert into `repos` table."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from github_traffic_vault.github_api import GitHubClient
from github_traffic_vault.models import Repo

log = logging.getLogger(__name__)


@dataclass
class DiscoveredRepo:
    repo: Repo
    is_new: bool


def discover_and_upsert(
    session: Session, gh: GitHubClient, now: datetime | None = None
) -> list[DiscoveredRepo]:
    now = now or datetime.now(UTC)
    payloads = gh.list_owned_repos()
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
            stargazers=stargazers,
            forks=forks,
            watchers=watchers,
            default_branch=default_branch,
            created_at=created_at,
            first_seen_at=now,
            last_seen_at=now,
        )
        session.add(repo)
        return DiscoveredRepo(repo=repo, is_new=True)

    existing.owner = owner
    existing.name = name
    existing.is_fork = is_fork
    existing.is_archived = is_archived
    existing.stargazers = stargazers
    existing.forks = forks
    existing.watchers = watchers
    existing.default_branch = default_branch
    if created_at is not None:
        existing.created_at = created_at
    existing.last_seen_at = now
    return DiscoveredRepo(repo=existing, is_new=False)


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
