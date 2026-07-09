"""Top-level CLI: argparse subcommands.

github-traffic-vault sync   - discover + sync (cron entry point)
github-traffic-vault repos  - list known repos
github-traffic-vault show   - per-repo daily table + latest top lists
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date as _date
from datetime import datetime, tzinfo

import uvicorn
from chalkbox import Section, Spinner, Table, get_console
from sqlalchemy import select
from sqlalchemy.orm import Session

import github_traffic_vault
from github_traffic_vault.config import Config, ensure_data_dir, load
from github_traffic_vault.db import init_schema, make_engine, session_scope
from github_traffic_vault.github_api import GitHubClient, TokenError, resolve_token
from github_traffic_vault.logging_setup import configure
from github_traffic_vault.models import (
    DailyClones,
    DailyViews,
    PathSnapshot,
    ReferrerSnapshot,
    Repo,
    RepoSync,
)
from github_traffic_vault.numfmt import compact_number
from github_traffic_vault.reports import export_rows, top_repos, top_repos_combined, write_csv, write_json
from github_traffic_vault.repos import discover_and_upsert
from github_traffic_vault.sync import SyncOptions, run_sync
from github_traffic_vault.timefmt import format_local
from github_traffic_vault.web.app import create_app

log = logging.getLogger("github_traffic_vault.cli")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="github-traffic-vault", description="GitHub traffic archiver")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging on stdout")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_sync = sub.add_parser("sync", help="discover + sync all owned repos")
    p_sync.add_argument("--only", action="append", default=None, metavar="OWNER/REPO")
    p_sync.add_argument("--dry-run", action="store_true")

    sub.add_parser("repos", help="list known repos")

    p_show = sub.add_parser("show", help="show daily series + latest top lists for one repo")
    p_show.add_argument("repo", help='"name" or "owner/name"')
    p_show.add_argument("--since", default=None, help="YYYY-MM-DD")

    p_export = sub.add_parser("export", help="export views/clones/referrers/paths")
    p_export.add_argument("repo", help='"all" or "name" or "owner/name"')
    p_export.add_argument("--format", choices=["csv", "json"], default="csv")
    p_export.add_argument("--kind", choices=["views", "clones", "referrers", "paths"], default="views")
    p_export.add_argument("--since", default=None)
    p_export.add_argument("-o", "--output", default=None, help="file path; default stdout")

    p_top = sub.add_parser("top", help="rank repos by traffic in a window")
    p_top.add_argument(
        "--by",
        choices=["views", "clones"],
        default=None,
        help="filter to one kind; omit to show both views and clones",
    )
    p_top.add_argument("--since", default=None)
    p_top.add_argument("--limit", type=int, default=10)

    p_serve = sub.add_parser("serve", help="run the web UI on http://host:port")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8800)
    p_serve.add_argument("--reload", action="store_true", help="dev: auto-reload on file change")

    args = parser.parse_args(argv)
    cfg = load()
    ensure_data_dir(cfg)
    configure(cfg.log_path, verbose=args.verbose)

    if args.cmd == "sync":
        return _cmd_sync(cfg, only=args.only, dry_run=args.dry_run)
    if args.cmd == "repos":
        return _cmd_repos(cfg)
    if args.cmd == "show":
        return _cmd_show(cfg, repo_ref=args.repo, since=_parse_since(args.since))
    if args.cmd == "export":
        return _cmd_export(
            cfg,
            repo_ref=args.repo,
            kind=args.kind,
            fmt=args.format,
            since=_parse_since(args.since),
            output=args.output,
        )
    if args.cmd == "top":
        return _cmd_top(cfg, by=args.by, since=_parse_since(args.since), limit=args.limit)
    if args.cmd == "serve":
        return _cmd_serve(cfg, host=args.host, port=args.port, reload=args.reload)
    parser.error(f"unknown command: {args.cmd}")


def _parse_since(s: str | None) -> _date | None:
    if s is None:
        return None
    return _date.fromisoformat(s)


def _cmd_sync(cfg: Config, only: list[str] | None, dry_run: bool) -> int:
    try:
        token = resolve_token(cfg.github_token_env)
    except TokenError as exc:
        log.error("auth: %s", exc)
        return 1

    engine = make_engine(cfg.db_path)
    init_schema(engine)

    if cfg.exclude_repos:
        log.info("Excluding repos: %s", ", ".join(sorted(cfg.exclude_repos)))

    console = get_console()
    with GitHubClient(token, cfg.user_agent) as gh, session_scope(engine) as session:
        with Spinner("Discovering repos...") as sp:
            results = discover_and_upsert(session, gh, exclude_repos=cfg.exclude_repos)
            repos = [r.repo for r in results]

            def _on_progress(repo: Repo, index: int, total: int) -> None:
                sp.update(f"[{index}/{total}] {repo.full_name}")

            run = run_sync(
                session,
                gh,
                cfg,
                SyncOptions(only_repos=only, dry_run=dry_run, exclude_repos=cfg.exclude_repos),
                repos,
                on_progress=_on_progress,
            )

        if dry_run:
            session.rollback()
            console.print(f"[yellow]dry-run:[/yellow] would have committed run id={run.id}")
            return 0

        rows = session.execute(
            select(RepoSync, Repo)
            .join(Repo, Repo.id == RepoSync.repo_id)
            .where(RepoSync.sync_run_id == run.id)
            .order_by(Repo.full_name)
        ).all()

        duration = (run.finished_at - run.started_at).total_seconds() if run.finished_at else 0.0
        subtitle = f"run #{compact_number(run.id)} - {len(rows)} repos in {duration:.1f}s"
        footer = f"ok={run.repos_ok}  err={run.repos_err}  rate_remaining={run.rate_remaining}"

        with Section("Sync", subtitle=subtitle, footer=footer) as section:
            table = Table(
                headers=["repo", "http", "v_chg", "c_chg", "top", "error"],
                row_styles="severity",
                expand=True,
            )
            for rs, repo in rows:
                changed = bool(
                    rs.views_changed_days
                    or rs.clones_changed_days
                    or rs.referrers_changed
                    or rs.paths_changed
                )
                if rs.error:
                    severity = "error"
                elif changed:
                    severity = "highlighted"
                else:
                    severity = "muted"
                table.add_row(
                    repo.full_name,
                    f"{rs.http_calls}/{rs.http_304s}",
                    str(rs.views_changed_days),
                    str(rs.clones_changed_days),
                    _changed_summary(rs.referrers_changed, rs.paths_changed),
                    rs.error or "",
                    severity=severity,
                )
            section.add(table)
    return 0


def _changed_summary(referrers_changed: bool, paths_changed: bool) -> str:
    parts = []
    if referrers_changed:
        parts.append("ref")
    if paths_changed:
        parts.append("path")
    return "+".join(parts) if parts else "-"


def _cmd_repos(cfg: Config) -> int:
    engine = make_engine(cfg.db_path)
    init_schema(engine)
    with session_scope(engine) as session:
        rows = session.scalars(select(Repo).order_by(Repo.full_name)).all()
        if cfg.exclude_repos:
            rows = [r for r in rows if r.name.lower() not in cfg.exclude_repos]
        with Section("Tracked repos", subtitle=f"{len(rows)} total") as section:
            table = Table(
                headers=["full_name", "last_synced_at", "last_change_at"],
                row_styles="alternate",
            )
            for r in rows:
                table.add_row(
                    r.full_name,
                    _fmt(r.last_synced_at, cfg.display_tz),
                    _fmt(r.last_traffic_change_at, cfg.display_tz),
                )
            section.add(table)
    return 0


def _cmd_show(cfg: Config, repo_ref: str, since: _date | None) -> int:
    engine = make_engine(cfg.db_path)
    with session_scope(engine) as session:
        repo = _resolve_repo(session, repo_ref)
        if repo is not None and cfg.exclude_repos and repo.name.lower() in cfg.exclude_repos:
            print(f"repo excluded via config: {repo_ref}", file=sys.stderr)
            return 2
        if repo is None:
            print(f"no such repo: {repo_ref}", file=sys.stderr)
            return 2

        v_q = select(DailyViews).where(DailyViews.repo_id == repo.id)
        c_q = select(DailyClones).where(DailyClones.repo_id == repo.id)
        if since:
            v_q = v_q.where(DailyViews.date >= since)
            c_q = c_q.where(DailyClones.date >= since)
        v_rows = {r.date: r for r in session.scalars(v_q).all()}
        c_rows = {r.date: r for r in session.scalars(c_q).all()}
        all_dates = sorted(set(v_rows) | set(c_rows))

        subtitle = f"since {since}" if since else "last 14 days"
        with Section(repo.full_name, subtitle=subtitle) as section:
            daily = Table(
                title="Daily traffic",
                headers=["date", "views", "v_uniq", "clones", "c_uniq"],
                row_styles="severity",
            )
            for d in all_dates:
                v = v_rows.get(d)
                c = c_rows.get(d)
                v_count = v.count if v else 0
                v_uniq = v.uniques if v else 0
                c_count = c.count if c else 0
                c_uniq = c.uniques if c else 0
                severity = "muted" if (v_count == 0 and c_count == 0) else "success"
                daily.add_row(
                    d.isoformat(),
                    str(v_count),
                    str(v_uniq),
                    str(c_count),
                    str(c_uniq),
                    severity=severity,
                )
            section.add(daily)

            ref_table = _top_referrers_table(session, repo.id)
            if ref_table is not None:
                section.add_spacing()
                section.add(ref_table)

            path_table = _top_paths_table(session, repo.id)
            if path_table is not None:
                section.add_spacing()
                section.add(path_table)
    return 0


def _top_referrers_table(session: Session, repo_id: int) -> Table | None:
    run_id = session.scalar(
        select(ReferrerSnapshot.sync_run_id)
        .where(ReferrerSnapshot.repo_id == repo_id)
        .order_by(ReferrerSnapshot.sync_run_id.desc())
        .limit(1)
    )
    if run_id is None:
        return None
    rows = session.scalars(
        select(ReferrerSnapshot).where(
            ReferrerSnapshot.repo_id == repo_id, ReferrerSnapshot.sync_run_id == run_id
        )
    ).all()
    table = Table(
        title=f"Top referrers (snapshot run_id={run_id})",
        headers=["referrer", "count", "uniques"],
        row_styles="alternate",
    )
    for r in rows:
        table.add_row(r.referrer, str(r.count), str(r.uniques))
    return table


def _top_paths_table(session: Session, repo_id: int) -> Table | None:
    run_id = session.scalar(
        select(PathSnapshot.sync_run_id)
        .where(PathSnapshot.repo_id == repo_id)
        .order_by(PathSnapshot.sync_run_id.desc())
        .limit(1)
    )
    if run_id is None:
        return None
    rows = session.scalars(
        select(PathSnapshot).where(PathSnapshot.repo_id == repo_id, PathSnapshot.sync_run_id == run_id)
    ).all()
    table = Table(
        title=f"Top paths (snapshot run_id={run_id})",
        headers=["path", "title", "count", "uniques"],
        row_styles="alternate",
    )
    for r in rows:
        table.add_row(r.path, r.title or "", str(r.count), str(r.uniques))
    return table


def _resolve_repo(session: Session, repo_ref: str) -> Repo | None:
    if "/" in repo_ref:
        return session.scalar(select(Repo).where(Repo.full_name == repo_ref))
    matches = session.scalars(select(Repo).where(Repo.name == repo_ref)).all()
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        names = ", ".join(r.full_name for r in matches)
        print(f"ambiguous: {repo_ref} matches {names}", file=sys.stderr)
        return None
    return None


def _fmt(dt: datetime | None, tz: tzinfo) -> str:
    return format_local(dt, tz) if dt else "-"


def _cmd_export(
    cfg: Config,
    repo_ref: str,
    kind: str,
    fmt: str,
    since: _date | None,
    output: str | None,
) -> int:
    engine = make_engine(cfg.db_path)
    with session_scope(engine) as session:
        full_name = None if repo_ref == "all" else _resolve_full_name(session, repo_ref)
        if repo_ref != "all" and full_name is None:
            print(f"no such repo: {repo_ref}", file=sys.stderr)
            return 2
        rows = export_rows(session, full_name, kind, since, exclude_repos=cfg.exclude_repos)

    if output:
        with open(output, "w", encoding="utf-8") as fh:
            if fmt == "csv":
                write_csv(rows, fh)
            else:
                write_json(rows, fh)
    else:
        if fmt == "csv":
            write_csv(rows, sys.stdout)
        else:
            write_json(rows, sys.stdout)
    return 0


def _cmd_top(cfg: Config, by: str | None, since: _date | None, limit: int) -> int:
    engine = make_engine(cfg.db_path)
    with session_scope(engine) as session:
        subtitle = f"since {since}" if since else "last 14 days"
        if by is None:
            combined = top_repos_combined(session, since, limit, exclude_repos=cfg.exclude_repos)
            with Section(f"Top {limit} repos (views + clones)", subtitle=subtitle) as section:
                table = Table(
                    headers=["full_name", "views", "v_uniq", "clones", "c_uniq"],
                    row_styles="alternate",
                )
                for full_name, v, vu, c, cu in combined:
                    table.add_row(full_name, str(v), str(vu), str(c), str(cu))
                section.add(table)
        else:
            ranked = top_repos(session, by, since, limit, exclude_repos=cfg.exclude_repos)
            with Section(f"Top {limit} repos by {by}", subtitle=subtitle) as section:
                table = Table(
                    headers=["full_name", "total", "uniques"],
                    row_styles="alternate",
                )
                for full_name, total, uniques in ranked:
                    table.add_row(full_name, str(total), str(uniques))
                section.add(table)
    return 0


def _resolve_full_name(session: Session, repo_ref: str) -> str | None:
    repo = _resolve_repo(session, repo_ref)
    return repo.full_name if repo else None


def _cmd_serve(cfg: Config, host: str, port: int, reload: bool) -> int:
    # proxy_headers + forwarded_allow_ips make uvicorn honor
    # X-Forwarded-Proto/-For from Traefik in front. Without these the
    # app sees plain http (which is what reaches the container) and
    # `url_for` emits http:// asset URLs on the https:// page -- browser
    # blocks them as mixed content.
    if reload:
        uvicorn.run(
            "github_traffic_vault.web.app:_reload_app_factory",
            host=host,
            port=port,
            reload=True,
            factory=True,
            reload_dirs=[str(github_traffic_vault.__path__[0])],
            proxy_headers=True,
            forwarded_allow_ips=cfg.forwarded_allow_ips,
        )
    else:
        uvicorn.run(
            create_app(cfg),
            host=host,
            port=port,
            proxy_headers=True,
            forwarded_allow_ips=cfg.forwarded_allow_ips,
        )
    return 0
