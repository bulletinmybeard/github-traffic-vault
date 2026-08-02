"""FastAPI app: one page + a sync trigger + a JSON endpoint."""

from __future__ import annotations

import logging
import re
import secrets
from collections.abc import Mapping
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from starlette.middleware.sessions import SessionMiddleware

from github_traffic_vault.config import Config
from github_traffic_vault.config import load as load_config
from github_traffic_vault.config_store import update_config_file
from github_traffic_vault.db import init_schema, make_engine, session_scope
from github_traffic_vault.github_api import GitHubClient, TokenError, resolve_token
from github_traffic_vault.local_git import (
    find_under_roots,
    inspect_local,
    list_browse,
    validate_link_path,
)
from github_traffic_vault.models import Repo
from github_traffic_vault.numfmt import compact_number
from github_traffic_vault.repos import discover_and_upsert
from github_traffic_vault.sync import SyncOptions, run_sync
from github_traffic_vault.timefmt import format_local, today_in_tz, traffic_today_utc
from github_traffic_vault.web.queries import (
    FILTER_OPTIONS,
    PERIOD_PRESETS,
    SORT_OPTIONS,
    archive_span_global,
    build_filter_options,
    earliest_data_date_global,
    filter_counts,
    index_query_string,
    latest_sync,
    repo_detail,
    repo_totals,
    repo_views,
    resolve_period,
    sort_filter_query_string,
)

_REPO_FULL_NAME_RE = re.compile(r"^[\w.-]+/[\w.-]+$")

log = logging.getLogger(__name__)


class _CfgRef:
    cfg: Config


_cfg_ref = _CfgRef()

_WEB_DIR = Path(__file__).parent
_TEMPLATES_DIR = _WEB_DIR / "templates"
_STATIC_DIR = _WEB_DIR / "static"


def create_app(cfg: Config) -> FastAPI:
    app = FastAPI(title="github-traffic-vault", docs_url=None, redoc_url=None)
    _cfg_ref.cfg = cfg

    if not cfg.secret_key_configured:
        log.warning(
            "auth.secret_key not set in config.yaml; using a random per-process "
            "secret. Sessions and CSRF tokens reset on every restart. Set "
            "auth.secret_key in production."
        )

    engine = make_engine(cfg.db_path)
    init_schema(engine)
    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
    templates.env.filters["relative_time"] = _relative_time

    def _localdt(value: datetime | None, fmt: str = "%Y-%m-%d %H:%M") -> str:
        """Format a stored UTC datetime in the configured display timezone."""
        if value is None:
            return ""
        return format_local(value, _cfg_ref.cfg.display_tz, fmt)

    templates.env.filters["localdt"] = _localdt
    templates.env.filters["compact"] = compact_number

    app.state.cfg = cfg
    app.state.engine = engine
    app.state.templates = templates

    app.add_middleware(
        SessionMiddleware,
        secret_key=cfg.secret_key,
        session_cookie="gtv_session",
        same_site="strict",
        https_only=cfg.secure_cookie,
    )
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon() -> RedirectResponse:
        return RedirectResponse(url="/static/favicon.svg")

    @app.middleware("http")
    async def no_store_html(request: Request, call_next):  # type: ignore[no-untyped-def]
        response = await call_next(request)
        ct = response.headers.get("content-type", "")
        if ct.startswith("text/html"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/", response_class=HTMLResponse)
    def index(
        request: Request,
        days: int | None = None,
        period_range: str | None = Query(default=None, alias="range"),
        date_from: str | None = Query(default=None, alias="from"),
        date_to: str | None = Query(default=None, alias="to"),
        sort: str = Query(default="created"),
        filter_: str = Query(default="public", alias="filter"),
        error: str | None = None,
    ) -> HTMLResponse:
        cfg = _cfg_ref.cfg
        csrf = _ensure_csrf(request)
        calendar_today = today_in_tz(cfg.display_tz)
        if sort not in {k for k, _ in SORT_OPTIONS}:
            sort = "created"
        with session_scope(engine) as session:
            sync = latest_sync(session)
            period = resolve_period(
                days=days,
                range_=period_range,
                date_from=date_from,
                date_to=date_to,
                earliest=earliest_data_date_global(session),
                today=calendar_today,
            )
            archive = archive_span_global(session)
            counts = filter_counts(session, exclude_repos=cfg.exclude_repos)
            filter_options = build_filter_options(counts)
            if filter_ not in {k for k, _ in FILTER_OPTIONS}:
                filter_ = "public"
            else:
                disabled = {str(o["key"]) for o in filter_options if o["disabled"]}
                if filter_ in disabled:
                    filter_ = "all"
            tiles = repo_totals(
                session,
                start=period.start,
                end=period.end,
                traffic_today=traffic_today_utc(),
                open_end=period.open_end,
                exclude_repos=cfg.exclude_repos,
                sort=sort,
                filter_=filter_,
                sparkline_days=cfg.sparkline_days,
                period=period,
                include_today=cfg.show_tile_today,
                include_sparklines=cfg.show_tile_sparklines,
            )
        sparkline_payload = (
            [
                {
                    "full_name": t.full_name,
                    "views": t.sparkline_views,
                    "clones": t.sparkline_clones,
                }
                for t in tiles
            ]
            if cfg.show_tile_sparklines
            else []
        )
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "sync": sync,
                "tiles": tiles,
                "period": period,
                "archive": archive,
                "presets": PERIOD_PRESETS,
                "flash_error": _take_flash_error(request) or error,
                "csrf_token": csrf,
                "tiles_per_row": cfg.tiles_per_row,
                "show_tile_today": cfg.show_tile_today,
                "show_tile_sparklines": cfg.show_tile_sparklines,
                "tile_sparklines_compact": cfg.tile_sparklines_compact,
                "sort": sort,
                "filter_": filter_,
                "sort_options": [{"key": k, "label": label} for k, label in SORT_OPTIONS],
                "filter_options": filter_options,
                "index_qs": index_query_string(period, sort, filter_),
                "qs_keep": sort_filter_query_string(sort, filter_),
                "sparkline_payload": sparkline_payload,
                "show_sync_meta": True,
                "show_sync_button": True,
                "show_repo_search": True,
                "settings_active": False,
                "sync_next": f"/?{index_query_string(period, sort, filter_)}",
            },
        )

    @app.post("/sync")
    def trigger_sync(
        request: Request,
        next_url: str = Form(default="/", alias="next"),
        csrf_token: str = Form(default=""),
        only: str = Form(default=""),
    ) -> Response:
        session_token = request.session.get("csrf_token")
        if not session_token or not secrets.compare_digest(csrf_token, session_token):
            return HTMLResponse("csrf token mismatch", status_code=403)

        cfg = _cfg_ref.cfg
        safe_next = next_url if next_url.startswith("/") and not next_url.startswith(("//", "/\\")) else "/"
        only_repo = only.strip()
        if only_repo and not _REPO_FULL_NAME_RE.fullmatch(only_repo):
            return HTMLResponse("invalid only parameter", status_code=400)

        try:
            token = resolve_token(cfg.github_token)
        except TokenError as exc:
            log.warning("sync via web: token error: %s", exc)
            _flash_error(request, str(exc))
            return RedirectResponse(url=safe_next, status_code=303)
        with GitHubClient(token, cfg.user_agent) as gh, session_scope(engine) as session:
            if only_repo:
                repo = session.scalar(select(Repo).where(Repo.full_name == only_repo))
                if repo is None:
                    _flash_error(request, f"unknown repo: {only_repo}")
                    return RedirectResponse(url=safe_next, status_code=303)
                run_sync(
                    session,
                    gh,
                    cfg,
                    SyncOptions(only_repos=[only_repo], exclude_repos=cfg.exclude_repos),
                    [repo],
                )
            else:
                results = discover_and_upsert(
                    session, gh, exclude_repos=cfg.exclude_repos, include_private=cfg.include_private
                )
                repos = [r.repo for r in results]
                run_sync(session, gh, cfg, SyncOptions(exclude_repos=cfg.exclude_repos), repos)

        return RedirectResponse(url=safe_next, status_code=303)

    @app.get("/api/repos.json")
    def api_repos(
        days: int | None = None,
        period_range: str | None = Query(default=None, alias="range"),
        date_from: str | None = Query(default=None, alias="from"),
        date_to: str | None = Query(default=None, alias="to"),
    ) -> JSONResponse:
        cfg = _cfg_ref.cfg
        calendar_today = today_in_tz(cfg.display_tz)
        with session_scope(engine) as session:
            period = resolve_period(
                days=days,
                range_=period_range,
                date_from=date_from,
                date_to=date_to,
                earliest=earliest_data_date_global(session),
                today=calendar_today,
            )
            archive = archive_span_global(session)
            payload: dict[str, Any] = {
                "sync": _dc_or_none(latest_sync(session)),
                "repos": [
                    _repo_to_dict(rv)
                    for rv in repo_views(
                        session,
                        start=period.start,
                        end=period.end,
                        open_end=period.open_end,
                        exclude_repos=cfg.exclude_repos,
                    )
                ],
                "archive": {
                    "earliest": archive.earliest.isoformat() if archive.earliest else None,
                    "latest": archive.latest.isoformat() if archive.latest else None,
                    "through": period.end.isoformat(),
                    "label": archive.label,
                    "range_label": archive.range_label(through=period.end),
                },
                "period": {
                    "start": period.start.isoformat(),
                    "end": period.end.isoformat(),
                    "kind": period.kind,
                    "days": period.days,
                    "label": period.label,
                },
            }
        return JSONResponse(payload)

    @app.get("/settings")
    def settings_redirect() -> RedirectResponse:
        return RedirectResponse("/settings/general", status_code=302)

    @app.get("/settings/general", response_class=HTMLResponse)
    def settings_general(request: Request, saved: int | None = None) -> HTMLResponse:
        cfg = _cfg_ref.cfg
        return templates.TemplateResponse(
            request,
            "settings/general.html",
            _settings_context(request, saved=bool(saved), form=_general_form(cfg)),
        )

    @app.post("/settings/general")
    def settings_general_save(
        request: Request,
        csrf_token: str = Form(default=""),
        display_tz: str = Form(default="UTC"),
        tiles_per_row: str = Form(default="5"),
        sparkline_days: str = Form(default="14"),
        exclude_repos: str = Form(default=""),
    ) -> Response:
        if not _csrf_ok(request, csrf_token):
            return HTMLResponse("csrf token mismatch", status_code=403)
        try:
            updates = _validate_general_form(display_tz, tiles_per_row, sparkline_days, exclude_repos)
        except ValueError as exc:
            cfg = _cfg_ref.cfg
            return templates.TemplateResponse(
                request,
                "settings/general.html",
                _settings_context(request, error=str(exc), form=_general_form(cfg)),
                status_code=400,
            )
        _save_settings(request.app, updates)
        return RedirectResponse("/settings/general?saved=1", status_code=303)

    @app.get("/settings/cards", response_class=HTMLResponse)
    def settings_cards(request: Request, saved: int | None = None) -> HTMLResponse:
        cfg = _cfg_ref.cfg
        return templates.TemplateResponse(
            request,
            "settings/cards.html",
            _settings_context(request, active_section="cards", saved=bool(saved), form=_cards_form(cfg)),
        )

    @app.post("/settings/cards")
    def settings_cards_save(
        request: Request,
        csrf_token: str = Form(default=""),
        show_tile_today: str | None = Form(default=None),
        show_tile_sparklines: str | None = Form(default=None),
        tile_sparklines_compact: str | None = Form(default=None),
    ) -> Response:
        if not _csrf_ok(request, csrf_token):
            return HTMLResponse("csrf token mismatch", status_code=403)
        updates = {
            "cards": {
                "show_today": bool(show_tile_today),
                "show_sparklines": bool(show_tile_sparklines),
                "sparklines_compact": bool(tile_sparklines_compact),
            }
        }
        _save_settings(request.app, updates)
        return RedirectResponse("/settings/cards?saved=1", status_code=303)

    @app.get("/settings/sync", response_class=HTMLResponse)
    def settings_sync(request: Request, saved: int | None = None) -> HTMLResponse:
        cfg = _cfg_ref.cfg
        return templates.TemplateResponse(
            request,
            "settings/sync.html",
            _settings_context(request, active_section="sync", saved=bool(saved), form=_sync_form(cfg)),
        )

    @app.post("/settings/sync")
    def settings_sync_save(
        request: Request,
        csrf_token: str = Form(default=""),
        include_private: str | None = Form(default=None),
    ) -> Response:
        if not _csrf_ok(request, csrf_token):
            return HTMLResponse("csrf token mismatch", status_code=403)
        updates = {"sync": {"include_private": bool(include_private)}}
        _save_settings(request.app, updates)
        return RedirectResponse("/settings/sync?saved=1", status_code=303)

    @app.get("/settings/local", response_class=HTMLResponse)
    def settings_local(request: Request, saved: int | None = None) -> HTMLResponse:
        cfg = _cfg_ref.cfg
        return templates.TemplateResponse(
            request,
            "settings/local.html",
            _settings_context(request, active_section="local", saved=bool(saved), form=_local_form(cfg)),
        )

    @app.post("/settings/local")
    def settings_local_save(
        request: Request,
        csrf_token: str = Form(default=""),
        roots: str = Form(default=""),
    ) -> Response:
        if not _csrf_ok(request, csrf_token):
            return HTMLResponse("csrf token mismatch", status_code=403)
        root_list = [line.strip() for line in roots.replace(",", "\n").splitlines() if line.strip()]
        _save_settings(request.app, {"local": {"roots": root_list}})
        return RedirectResponse("/settings/local?saved=1", status_code=303)

    @app.get("/api/local/browse")
    def api_local_browse(path: str | None = None) -> JSONResponse:
        cfg = _cfg_ref.cfg
        listing = list_browse(path, cfg.local_roots)
        return JSONResponse(
            {
                "path": listing.path,
                "parent": listing.parent,
                "error": listing.error,
                "entries": [
                    {
                        "name": e.name,
                        "path": e.path,
                        "is_dir": e.is_dir,
                        "is_git": e.is_git,
                    }
                    for e in listing.entries
                ],
            }
        )

    @app.get("/api/local/find")
    def api_local_find(full_name: str = Query(...)) -> JSONResponse:
        cfg = _cfg_ref.cfg
        if not _REPO_FULL_NAME_RE.fullmatch(full_name):
            return JSONResponse({"error": "invalid full_name"}, status_code=400)
        matches = find_under_roots(cfg.local_roots, full_name)
        return JSONResponse({"matches": matches})

    @app.post("/{owner}/{repo_name}/local/link")
    def local_link(
        request: Request,
        owner: str,
        repo_name: str,
        path: str = Form(default=""),
        csrf_token: str = Form(default=""),
        next_url: str = Form(default="", alias="next"),
    ) -> Response:
        if not _csrf_ok(request, csrf_token):
            return HTMLResponse("csrf token mismatch", status_code=403)
        cfg = _cfg_ref.cfg
        full_name = f"{owner}/{repo_name}"
        safe_next = (
            next_url
            if next_url.startswith("/") and not next_url.startswith(("//", "/\\"))
            else f"/{full_name}"
        )
        resolved, err = validate_link_path(path, cfg.local_roots, full_name)
        if err:
            _flash_error(request, err)
            return RedirectResponse(url=safe_next, status_code=303)
        with session_scope(engine) as session:
            repo = session.scalar(select(Repo).where(Repo.full_name == full_name))
            if repo is None:
                return PlainTextResponse(f"no such repo: {full_name}", status_code=404)
            repo.local_path = str(resolved)
        return RedirectResponse(url=safe_next, status_code=303)

    @app.post("/{owner}/{repo_name}/local/unlink")
    def local_unlink(
        request: Request,
        owner: str,
        repo_name: str,
        csrf_token: str = Form(default=""),
        next_url: str = Form(default="", alias="next"),
    ) -> Response:
        if not _csrf_ok(request, csrf_token):
            return HTMLResponse("csrf token mismatch", status_code=403)
        full_name = f"{owner}/{repo_name}"
        safe_next = (
            next_url
            if next_url.startswith("/") and not next_url.startswith(("//", "/\\"))
            else f"/{full_name}"
        )
        with session_scope(engine) as session:
            repo = session.scalar(select(Repo).where(Repo.full_name == full_name))
            if repo is None:
                return PlainTextResponse(f"no such repo: {full_name}", status_code=404)
            repo.local_path = None
        return RedirectResponse(url=safe_next, status_code=303)

    @app.get("/{owner}/{repo_name}", response_class=HTMLResponse)
    def detail(
        request: Request,
        owner: str,
        repo_name: str,
        days: int | None = None,
        period_range: str | None = Query(default=None, alias="range"),
        date_from: str | None = Query(default=None, alias="from"),
        date_to: str | None = Query(default=None, alias="to"),
        error: str | None = None,
    ) -> Response:
        cfg = _cfg_ref.cfg
        csrf = _ensure_csrf(request)
        full_name = f"{owner}/{repo_name}"
        calendar_today = today_in_tz(cfg.display_tz)
        with session_scope(engine) as session:
            view = repo_detail(
                session,
                full_name,
                today=calendar_today,
                days=days,
                range_=period_range,
                date_from=date_from,
                date_to=date_to,
                exclude_repos=cfg.exclude_repos,
            )
        if view is None:
            return PlainTextResponse(f"no such repo: {full_name}", status_code=404)
        chart_data = [
            {
                "date": d.date.isoformat(),
                "views": d.views,
                "v_uniques": d.v_uniques,
                "clones": d.clones,
                "c_uniques": d.c_uniques,
            }
            for d in view.daily
        ]
        local = inspect_local(view.local_path, cfg.local_roots, full_name)
        flash_error = _take_flash_error(request) or error
        return templates.TemplateResponse(
            request,
            "detail.html",
            {
                "repo": view,
                "period": view.period,
                "presets": PERIOD_PRESETS,
                "flash_error": flash_error,
                "chart_data": chart_data,
                "csrf_token": csrf,
                "show_sync_meta": False,
                "show_sync_button": True,
                "show_repo_search": False,
                "settings_active": False,
                "sync_next": f"/{full_name}?{view.period.query_string if view.period else ''}",
                "sync_only": full_name,
                "local": local,
                "local_roots_configured": bool(cfg.local_roots),
            },
        )

    return app


def _config_path(cfg: Config) -> Path:
    return cfg.config_path if cfg.config_path.is_absolute() else Path.cwd() / cfg.config_path


def _tz_name(cfg: Config) -> str:
    key = getattr(cfg.display_tz, "key", None)
    return str(key) if key else "UTC"


def _general_form(cfg: Config) -> dict[str, str | int | bool]:
    return {
        "display_tz": _tz_name(cfg),
        "tiles_per_row": cfg.tiles_per_row,
        "sparkline_days": cfg.sparkline_days,
        "exclude_repos": ", ".join(sorted(cfg.exclude_repos)),
    }


def _cards_form(cfg: Config) -> dict[str, bool]:
    return {
        "show_tile_today": cfg.show_tile_today,
        "show_tile_sparklines": cfg.show_tile_sparklines,
        "tile_sparklines_compact": cfg.tile_sparklines_compact,
    }


def _sync_form(cfg: Config) -> dict[str, bool]:
    return {"include_private": cfg.include_private}


def _local_form(cfg: Config) -> dict[str, str]:
    return {"roots": "\n".join(str(p) for p in cfg.local_roots)}


def _settings_context(request: Request, **extra: Any) -> dict[str, Any]:
    return {
        "csrf_token": _ensure_csrf(request),
        "active_section": extra.pop("active_section", "general"),
        "settings_active": True,
        "show_sync_button": True,
        "show_sync_meta": False,
        "show_repo_search": False,
        "sync_next": "/settings/general",
        "saved": extra.pop("saved", False),
        "error": extra.pop("error", None),
        **extra,
    }


def _validate_general_form(
    display_tz: str,
    tiles_per_row: str,
    sparkline_days: str,
    exclude_repos: str,
) -> dict[str, object]:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    name = display_tz.strip() or "UTC"
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"invalid timezone: {display_tz}") from exc
    try:
        tpr = int(tiles_per_row)
    except ValueError as exc:
        raise ValueError("tiles per row must be a number") from exc
    if tpr < 1 or tpr > 5:
        raise ValueError("tiles per row must be 1-5")
    try:
        spd = int(sparkline_days)
    except ValueError as exc:
        raise ValueError("sparkline days must be a number") from exc
    if spd < 7 or spd > 30:
        raise ValueError("sparkline days must be 7-30")
    excluded = [n.strip().lower() for n in exclude_repos.split(",") if n.strip()]
    return {
        "display": {"timezone": name, "tiles_per_row": tpr},
        "cards": {"sparkline_days": spd},
        "sync": {"exclude_repos": excluded},
    }


def _save_settings(app: FastAPI, updates: Mapping[str, object]) -> None:
    cfg = _cfg_ref.cfg
    path = _config_path(cfg)
    update_config_file(path, dict(updates))
    new_cfg = load_config(config_path=path)
    _cfg_ref.cfg = new_cfg
    app.state.cfg = new_cfg


def _csrf_ok(request: Request, csrf_token: str) -> bool:
    session_token = request.session.get("csrf_token")
    return bool(session_token and secrets.compare_digest(csrf_token, session_token))


def _flash_error(request: Request, message: str) -> None:
    """Store a one-shot error for the next page render (toast, not URL)."""
    request.session["flash_error"] = message


def _take_flash_error(request: Request) -> str | None:
    msg = request.session.pop("flash_error", None)
    return str(msg) if msg else None


def _repo_to_dict(rv: Any) -> dict[str, Any]:
    return {
        "full_name": rv.full_name,
        "is_private": rv.is_private,
        "total_views": rv.total_views,
        "total_v_uniques": rv.total_v_uniques,
        "total_clones": rv.total_clones,
        "total_c_uniques": rv.total_c_uniques,
        "delta_views": rv.delta_views,
        "delta_clones": rv.delta_clones,
        "days_by_month": [
            {
                "month": label,
                "days": [
                    {
                        "date": d.date.isoformat(),
                        "views": d.views,
                        "v_uniques": d.v_uniques,
                        "clones": d.clones,
                        "c_uniques": d.c_uniques,
                    }
                    for d in days
                ],
            }
            for label, days in rv.days_by_month
        ],
    }


def _dc_or_none(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    out: dict[str, Any] = {}
    for k, v in asdict(value).items():
        out[k] = v.isoformat() if hasattr(v, "isoformat") else v
    return out


def _ensure_csrf(request: Request) -> str:
    """Read or mint a CSRF token in the session. Returns it for template use."""
    token = request.session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf_token"] = token
    return token


def _relative_time(value: datetime | None) -> str:
    """Short relative string ('just now', '12m ago', '3h ago', '5d ago')."""
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    secs = int((datetime.now(UTC) - value).total_seconds())
    if secs < 60:
        return "just now"
    mins = secs // 60
    if mins < 60:
        return f"{mins}m ago"
    hours = mins // 60
    if hours < 24:
        return f"{hours}h ago"
    return f"{hours // 24}d ago"


def _reload_app_factory() -> FastAPI:
    """uvicorn --reload entry point. Rebuilds the app on each reload."""
    return create_app(load_config())
