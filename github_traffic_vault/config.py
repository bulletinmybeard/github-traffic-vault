"""Resolved configuration from ``config.yaml``."""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from datetime import UTC, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from github_traffic_vault.config_store import DEFAULT_CONFIG, read_config_file, write_config_file

log = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path("config.yaml")


def _resolve_config_path(config_path: Path | str | None) -> Path:
    if config_path is None:
        return Path.cwd() / DEFAULT_CONFIG_PATH
    path = Path(config_path)
    return path if path.is_absolute() else Path.cwd() / path


def _as_bool(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return default


def _as_int(value: object, default: int) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


def _as_str(value: object, default: str = "") -> str:
    if value is None:
        return default
    return str(value).strip()


def _as_repo_set(value: object) -> frozenset[str]:
    if value is None:
        return frozenset()
    if isinstance(value, str):
        items = [name.strip().lower() for name in value.split(",") if name.strip()]
        return frozenset(items)
    if isinstance(value, list):
        return frozenset(str(name).strip().lower() for name in value if str(name).strip())
    return frozenset()


def _as_path_list(value: object) -> tuple[Path, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        items = [line.strip() for line in value.replace(",", "\n").splitlines() if line.strip()]
    elif isinstance(value, list):
        items = [str(item).strip() for item in value if str(item).strip()]
    else:
        return ()
    return tuple(Path(item).expanduser() for item in items)


def _resolve_tz(name: str) -> tzinfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        log.warning("invalid/unavailable timezone %r; using UTC", name)
        return UTC


@dataclass(frozen=True)
class Config:
    db_path: Path
    log_path: Path
    rate_limit_floor: int
    user_agent: str
    github_token: str
    secret_key: str
    secret_key_configured: bool
    secure_cookie: bool
    forwarded_allow_ips: str
    exclude_repos: frozenset[str]
    include_private: bool
    config_path: Path
    sparkline_days: int
    tiles_per_row: int
    show_tile_today: bool
    show_tile_sparklines: bool
    tile_sparklines_compact: bool
    display_tz: tzinfo
    local_roots: tuple[Path, ...]


def _ensure_config_file(path: Path) -> dict[str, object]:
    if path.is_file():
        return read_config_file(path)
    write_config_file(path, DEFAULT_CONFIG)
    log.info("created default config at %s", path)
    return read_config_file(path)


def load(*, config_path: Path | str | None = None) -> Config:
    resolved = _resolve_config_path(config_path)
    data = _ensure_config_file(resolved)

    auth = data.get("auth", {})
    display = data.get("display", {})
    cards = data.get("cards", {})
    sync = data.get("sync", {})
    paths = data.get("paths", {})
    server = data.get("server", {})
    local = data.get("local", {})

    if not isinstance(auth, dict):
        auth = {}
    if not isinstance(display, dict):
        display = {}
    if not isinstance(cards, dict):
        cards = {}
    if not isinstance(sync, dict):
        sync = {}
    if not isinstance(paths, dict):
        paths = {}
    if not isinstance(server, dict):
        server = {}
    if not isinstance(local, dict):
        local = {}

    root = Path.cwd()
    default_data = root / "data"
    configured_secret = _as_str(auth.get("secret_key"))
    secret_key = configured_secret or secrets.token_urlsafe(32)

    db_raw = _as_str(paths.get("db"), str(default_data / "github-traffic-vault.db"))
    log_raw = _as_str(paths.get("log"), str(default_data / "github-traffic-vault.log"))
    tz_name = _as_str(display.get("timezone"), "UTC")

    return Config(
        db_path=Path(db_raw) if Path(db_raw).is_absolute() else root / db_raw,
        log_path=Path(log_raw) if Path(log_raw).is_absolute() else root / log_raw,
        rate_limit_floor=_as_int(server.get("rate_limit_floor"), 100),
        user_agent=_as_str(server.get("user_agent"), "github-traffic-vault/0.1 (+local)"),
        github_token=_as_str(auth.get("github_token")),
        secret_key=secret_key,
        secret_key_configured=bool(configured_secret),
        secure_cookie=_as_bool(server.get("secure_cookie"), False),
        forwarded_allow_ips=_as_str(server.get("forwarded_allow_ips"), "127.0.0.1"),
        exclude_repos=_as_repo_set(sync.get("exclude_repos")),
        include_private=_as_bool(sync.get("include_private"), False),
        config_path=resolved,
        sparkline_days=max(7, min(30, _as_int(cards.get("sparkline_days"), 14))),
        tiles_per_row=max(1, min(5, _as_int(display.get("tiles_per_row"), 5))),
        show_tile_today=_as_bool(cards.get("show_today"), True),
        show_tile_sparklines=_as_bool(cards.get("show_sparklines"), True),
        tile_sparklines_compact=_as_bool(cards.get("sparklines_compact"), False),
        display_tz=_resolve_tz(tz_name),
        local_roots=_as_path_list(local.get("roots")),
    )


def ensure_data_dir(config: Config) -> None:
    config.db_path.parent.mkdir(parents=True, exist_ok=True)
    config.log_path.parent.mkdir(parents=True, exist_ok=True)
