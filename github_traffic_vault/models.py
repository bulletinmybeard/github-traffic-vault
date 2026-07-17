"""Declarative ORM models."""

from __future__ import annotations

from datetime import date as _date
from datetime import datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class SyncRun(Base):
    __tablename__ = "sync_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    repos_total: Mapped[int] = mapped_column(Integer, default=0)
    repos_ok: Mapped[int] = mapped_column(Integer, default=0)
    repos_err: Mapped[int] = mapped_column(Integer, default=0)
    rate_remaining: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notes: Mapped[str | None] = mapped_column(String, nullable=True)


class Repo(Base):
    __tablename__ = "repos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner: Mapped[str] = mapped_column(String)
    name: Mapped[str] = mapped_column(String)
    full_name: Mapped[str] = mapped_column(String, unique=True)
    is_fork: Mapped[bool] = mapped_column(Boolean, default=False)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False)
    is_private: Mapped[bool] = mapped_column(Boolean, default=False)
    stargazers: Mapped[int] = mapped_column(Integer, default=0)
    forks: Mapped[int] = mapped_column(Integer, default=0)
    watchers: Mapped[int] = mapped_column(Integer, default=0)
    default_branch: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_traffic_change_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # status snapshot (CI / release / open PRs) -- populated by sync, not discovery
    ci_status: Mapped[str | None] = mapped_column(String, nullable=True)
    ci_conclusion: Mapped[str | None] = mapped_column(String, nullable=True)
    ci_workflow: Mapped[str | None] = mapped_column(String, nullable=True)
    ci_branch: Mapped[str | None] = mapped_column(String, nullable=True)
    ci_run_url: Mapped[str | None] = mapped_column(String, nullable=True)
    ci_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    release_kind: Mapped[str | None] = mapped_column(String, nullable=True)
    release_name: Mapped[str | None] = mapped_column(String, nullable=True)
    release_url: Mapped[str | None] = mapped_column(String, nullable=True)
    release_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    open_pr_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # container-visible path to a local checkout (under config local.roots)
    local_path: Mapped[str | None] = mapped_column(String, nullable=True)


class RepoSync(Base):
    __tablename__ = "repo_syncs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sync_run_id: Mapped[int] = mapped_column(ForeignKey("sync_runs.id"))
    repo_id: Mapped[int] = mapped_column(ForeignKey("repos.id"))
    started_at: Mapped[datetime] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    views_changed_days: Mapped[int] = mapped_column(Integer, default=0)
    clones_changed_days: Mapped[int] = mapped_column(Integer, default=0)
    referrers_changed: Mapped[bool] = mapped_column(Boolean, default=False)
    paths_changed: Mapped[bool] = mapped_column(Boolean, default=False)
    http_calls: Mapped[int] = mapped_column(Integer, default=0)
    http_304s: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(String, nullable=True)


class DailyViews(Base):
    __tablename__ = "daily_views"

    repo_id: Mapped[int] = mapped_column(ForeignKey("repos.id"), primary_key=True)
    date: Mapped[_date] = mapped_column(Date, primary_key=True)
    count: Mapped[int] = mapped_column(Integer)
    uniques: Mapped[int] = mapped_column(Integer)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime)
    last_changed_at: Mapped[datetime] = mapped_column(DateTime)
    change_count: Mapped[int] = mapped_column(Integer, default=1)


class DailyClones(Base):
    __tablename__ = "daily_clones"

    repo_id: Mapped[int] = mapped_column(ForeignKey("repos.id"), primary_key=True)
    date: Mapped[_date] = mapped_column(Date, primary_key=True)
    count: Mapped[int] = mapped_column(Integer)
    uniques: Mapped[int] = mapped_column(Integer)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime)
    last_changed_at: Mapped[datetime] = mapped_column(DateTime)
    change_count: Mapped[int] = mapped_column(Integer, default=1)


class ReferrerSnapshot(Base):
    __tablename__ = "referrer_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sync_run_id: Mapped[int] = mapped_column(ForeignKey("sync_runs.id"))
    repo_id: Mapped[int] = mapped_column(ForeignKey("repos.id"))
    referrer: Mapped[str] = mapped_column(String)
    count: Mapped[int] = mapped_column(Integer)
    uniques: Mapped[int] = mapped_column(Integer)


class PathSnapshot(Base):
    __tablename__ = "path_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sync_run_id: Mapped[int] = mapped_column(ForeignKey("sync_runs.id"))
    repo_id: Mapped[int] = mapped_column(ForeignKey("repos.id"))
    path: Mapped[str] = mapped_column(String)
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    count: Mapped[int] = mapped_column(Integer)
    uniques: Mapped[int] = mapped_column(Integer)


class ChangeEvent(Base):
    __tablename__ = "change_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sync_run_id: Mapped[int] = mapped_column(ForeignKey("sync_runs.id"))
    repo_id: Mapped[int] = mapped_column(ForeignKey("repos.id"))
    kind: Mapped[str] = mapped_column(String)
    date: Mapped[_date] = mapped_column(Date)
    prev_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    new_count: Mapped[int] = mapped_column(Integer)
    count_delta: Mapped[int] = mapped_column(Integer)
    prev_uniques: Mapped[int | None] = mapped_column(Integer, nullable=True)
    new_uniques: Mapped[int] = mapped_column(Integer)
    uniques_delta: Mapped[int] = mapped_column(Integer)
    recorded_at: Mapped[datetime] = mapped_column(DateTime)


class Etag(Base):
    __tablename__ = "etags"
    __table_args__ = (UniqueConstraint("repo_id", "endpoint", name="uq_etag_repo_endpoint"),)

    repo_id: Mapped[int] = mapped_column(ForeignKey("repos.id"), primary_key=True)
    endpoint: Mapped[str] = mapped_column(String, primary_key=True)
    etag: Mapped[str | None] = mapped_column(String, nullable=True)
    last_modified: Mapped[str | None] = mapped_column(String, nullable=True)
    last_fetched_at: Mapped[datetime] = mapped_column(DateTime)


__all__ = [
    "Base",
    "ChangeEvent",
    "DailyClones",
    "DailyViews",
    "Etag",
    "PathSnapshot",
    "ReferrerSnapshot",
    "Repo",
    "RepoSync",
    "SyncRun",
]
