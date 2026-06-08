"""Read-only queries for the `export` and `top` subcommands."""

from __future__ import annotations

import csv
import json
from datetime import date as _date
from typing import IO, Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from github_traffic_vault.models import DailyClones, DailyViews, PathSnapshot, ReferrerSnapshot, Repo


def export_rows(
    session: Session,
    repo_full_name: str | None,
    kind: str,
    since: _date | None,
    exclude_repos: frozenset[str] | None = None,
) -> list[dict[str, Any]]:
    """kind: views | clones | referrers | paths. `repo_full_name` None = all repos."""
    if kind == "views":
        return _export_daily(session, DailyViews, repo_full_name, since, exclude_repos)
    if kind == "clones":
        return _export_daily(session, DailyClones, repo_full_name, since, exclude_repos)
    if kind == "referrers":
        return _export_referrers(session, repo_full_name, exclude_repos)
    if kind == "paths":
        return _export_paths(session, repo_full_name, exclude_repos)
    raise ValueError(f"unknown kind: {kind}")


def _export_daily(
    session: Session,
    model: type[DailyViews] | type[DailyClones],
    repo_full_name: str | None,
    since: _date | None,
    exclude_repos: frozenset[str] | None = None,
) -> list[dict[str, Any]]:
    q = select(model, Repo).join(Repo, Repo.id == model.repo_id)
    if repo_full_name is not None:
        q = q.where(Repo.full_name == repo_full_name)
    if since is not None:
        q = q.where(model.date >= since)
    if exclude_repos:
        q = q.where(func.lower(Repo.name).notin_(list(exclude_repos)))
    rows: list[dict[str, Any]] = []
    for row, repo in session.execute(q).all():
        rows.append(
            {
                "full_name": repo.full_name,
                "date": row.date.isoformat(),
                "count": row.count,
                "uniques": row.uniques,
                "first_seen_at": row.first_seen_at.isoformat(),
                "last_changed_at": row.last_changed_at.isoformat(),
                "change_count": row.change_count,
            }
        )
    return rows


def _export_referrers(
    session: Session,
    repo_full_name: str | None,
    exclude_repos: frozenset[str] | None = None,
) -> list[dict[str, Any]]:
    q = select(ReferrerSnapshot, Repo).join(Repo, Repo.id == ReferrerSnapshot.repo_id)
    if repo_full_name is not None:
        q = q.where(Repo.full_name == repo_full_name)
    if exclude_repos:
        q = q.where(func.lower(Repo.name).notin_(list(exclude_repos)))
    return [
        {
            "full_name": repo.full_name,
            "sync_run_id": row.sync_run_id,
            "referrer": row.referrer,
            "count": row.count,
            "uniques": row.uniques,
        }
        for row, repo in session.execute(q).all()
    ]


def _export_paths(
    session: Session,
    repo_full_name: str | None,
    exclude_repos: frozenset[str] | None = None,
) -> list[dict[str, Any]]:
    q = select(PathSnapshot, Repo).join(Repo, Repo.id == PathSnapshot.repo_id)
    if repo_full_name is not None:
        q = q.where(Repo.full_name == repo_full_name)
    if exclude_repos:
        q = q.where(func.lower(Repo.name).notin_(list(exclude_repos)))
    return [
        {
            "full_name": repo.full_name,
            "sync_run_id": row.sync_run_id,
            "path": row.path,
            "title": row.title,
            "count": row.count,
            "uniques": row.uniques,
        }
        for row, repo in session.execute(q).all()
    ]


def write_csv(rows: list[dict[str, Any]], dest: IO[str]) -> None:
    if not rows:
        return
    writer = csv.DictWriter(dest, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)


def write_json(rows: list[dict[str, Any]], dest: IO[str]) -> None:
    json.dump(rows, dest, indent=2, default=str)
    dest.write("\n")


def top_repos(
    session: Session,
    by: str,
    since: _date | None,
    limit: int,
    exclude_repos: frozenset[str] | None = None,
) -> list[tuple[str, int, int]]:
    """Returns [(full_name, total_count, total_uniques)] sorted desc by count."""
    if by == "views":
        model: type[DailyViews] | type[DailyClones] = DailyViews
    elif by == "clones":
        model = DailyClones
    else:
        raise ValueError(f"unknown by: {by}")

    q = (
        select(
            Repo.full_name,
            func.coalesce(func.sum(model.count), 0).label("total_count"),
            func.coalesce(func.sum(model.uniques), 0).label("total_uniques"),
        )
        .join(Repo, Repo.id == model.repo_id)
        .group_by(Repo.full_name)
        .order_by(func.coalesce(func.sum(model.count), 0).desc())
        .limit(limit)
    )
    if since is not None:
        q = q.where(model.date >= since)
    if exclude_repos:
        q = q.where(func.lower(Repo.name).notin_(list(exclude_repos)))
    return [(r[0], int(r[1]), int(r[2])) for r in session.execute(q).all()]


def top_repos_combined(
    session: Session,
    since: _date | None,
    limit: int,
    exclude_repos: frozenset[str] | None = None,
) -> list[tuple[str, int, int, int, int]]:
    """Returns [(full_name, views, v_uniques, clones, c_uniques)] sorted desc by views."""
    v_q = select(
        DailyViews.repo_id,
        func.coalesce(func.sum(DailyViews.count), 0).label("v_total"),
        func.coalesce(func.sum(DailyViews.uniques), 0).label("v_uniques"),
    ).group_by(DailyViews.repo_id)
    c_q = select(
        DailyClones.repo_id,
        func.coalesce(func.sum(DailyClones.count), 0).label("c_total"),
        func.coalesce(func.sum(DailyClones.uniques), 0).label("c_uniques"),
    ).group_by(DailyClones.repo_id)
    if since is not None:
        v_q = v_q.where(DailyViews.date >= since)
        c_q = c_q.where(DailyClones.date >= since)
    v_sub = v_q.subquery()
    c_sub = c_q.subquery()

    q = (
        select(
            Repo.full_name,
            func.coalesce(v_sub.c.v_total, 0).label("v_total"),
            func.coalesce(v_sub.c.v_uniques, 0).label("v_uniques"),
            func.coalesce(c_sub.c.c_total, 0).label("c_total"),
            func.coalesce(c_sub.c.c_uniques, 0).label("c_uniques"),
        )
        .outerjoin(v_sub, v_sub.c.repo_id == Repo.id)
        .outerjoin(c_sub, c_sub.c.repo_id == Repo.id)
        .order_by(func.coalesce(v_sub.c.v_total, 0).desc())
        .limit(limit)
    )
    if exclude_repos:
        q = q.where(func.lower(Repo.name).notin_(list(exclude_repos)))
    return [(r[0], int(r[1]), int(r[2]), int(r[3]), int(r[4])) for r in session.execute(q).all()]
