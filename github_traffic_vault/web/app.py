"""FastAPI app: one page + a sync trigger + a JSON endpoint."""

from __future__ import annotations

import logging
import secrets
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import FastAPI, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from github_traffic_vault.config import Config
from github_traffic_vault.config import load as load_config
from github_traffic_vault.db import init_schema, make_engine, session_scope
from github_traffic_vault.github_api import GitHubClient, TokenError, resolve_token
from github_traffic_vault.repos import discover_and_upsert
from github_traffic_vault.sync import SyncOptions, run_sync
from github_traffic_vault.web.queries import latest_sync, repo_detail, repo_totals, repo_views

log = logging.getLogger(__name__)

_WEB_DIR = Path(__file__).parent
_TEMPLATES_DIR = _WEB_DIR / "templates"
_STATIC_DIR = _WEB_DIR / "static"


def create_app(cfg: Config) -> FastAPI:
    app = FastAPI(title="github-traffic-vault", docs_url=None, redoc_url=None)

    if not cfg.secret_key_from_env:
        log.warning(
            "GITHUB_TRAFFIC_VAULT_SECRET_KEY not set; using a random per-process "
            "secret. Sessions and CSRF tokens reset on every restart. Set the env "
            "var in production."
        )

    engine = make_engine(cfg.db_path)
    init_schema(engine)
    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
    templates.env.filters["relative_time"] = _relative_time

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
    def index(request: Request, days: int = 14, error: str | None = None) -> HTMLResponse:
        csrf = _ensure_csrf(request)
        with session_scope(engine) as session:
            sync = latest_sync(session)
            tiles = repo_totals(session, days=days, exclude_repos=cfg.exclude_repos)
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "sync": sync,
                "tiles": tiles,
                "days": days,
                "error": error,
                "csrf_token": csrf,
                "tiles_per_row": cfg.tiles_per_row,
            },
        )

    @app.post("/sync")
    def trigger_sync(
        request: Request,
        next_url: str = Form(default="/", alias="next"),
        csrf_token: str = Form(default=""),
    ) -> Response:
        session_token = request.session.get("csrf_token")
        if not session_token or not secrets.compare_digest(csrf_token, session_token):
            return HTMLResponse("csrf token mismatch", status_code=403)

        safe_next = next_url if next_url.startswith("/") and not next_url.startswith(("//", "/\\")) else "/"
        try:
            token = resolve_token(cfg.github_token_env)
        except TokenError as exc:
            log.warning("sync via web: token error: %s", exc)
            sep = "&" if "?" in safe_next else "?"
            return RedirectResponse(url=f"{safe_next}{sep}error={quote(str(exc), safe='')}", status_code=303)

        with GitHubClient(token, cfg.user_agent) as gh, session_scope(engine) as session:
            results = discover_and_upsert(session, gh, exclude_repos=cfg.exclude_repos)
            repos = [r.repo for r in results]
            run_sync(session, gh, cfg, SyncOptions(exclude_repos=cfg.exclude_repos), repos)

        return RedirectResponse(url=safe_next, status_code=303)

    @app.get("/api/repos.json")
    def api_repos(days: int = 14) -> JSONResponse:
        with session_scope(engine) as session:
            payload: dict[str, Any] = {
                "sync": _dc_or_none(latest_sync(session)),
                "repos": [
                    _repo_to_dict(rv)
                    for rv in repo_views(session, days=days, exclude_repos=cfg.exclude_repos)
                ],
                "days": days,
            }
        return JSONResponse(payload)

    @app.get("/{owner}/{repo_name}", response_class=HTMLResponse)
    def detail(
        request: Request,
        owner: str,
        repo_name: str,
        days: int = 14,
        period_range: str | None = Query(default=None, alias="range"),
        date_from: str | None = Query(default=None, alias="from"),
        date_to: str | None = Query(default=None, alias="to"),
        error: str | None = None,
    ) -> Response:
        csrf = _ensure_csrf(request)
        full_name = f"{owner}/{repo_name}"
        with session_scope(engine) as session:
            view = repo_detail(
                session,
                full_name,
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
        return templates.TemplateResponse(
            request,
            "detail.html",
            {
                "repo": view,
                "presets": (7, 14, 30, 90),
                "error": error,
                "chart_data": chart_data,
                "csrf_token": csrf,
            },
        )

    return app


def _repo_to_dict(rv: Any) -> dict[str, Any]:
    return {
        "full_name": rv.full_name,
        "total_views": rv.total_views,
        "total_v_uniques": rv.total_v_uniques,
        "total_clones": rv.total_clones,
        "total_c_uniques": rv.total_c_uniques,
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
