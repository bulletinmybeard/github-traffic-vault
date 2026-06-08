"""Resolved configuration. Env vars > built-in defaults."""

from __future__ import annotations

import logging
import os
import secrets
from dataclasses import dataclass
from datetime import UTC, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

log = logging.getLogger(__name__)


def _env(key: str, default: str) -> str:
    val = os.environ.get(key)
    return val if val else default


def _env_int(key: str, default: int) -> int:
    raw = os.environ.get(key)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_bool(key: str, default: bool) -> bool:
    raw = os.environ.get(key)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_set(key: str) -> frozenset[str]:
    raw = os.environ.get(key)
    if not raw:
        return frozenset()
    return frozenset(name.strip().lower() for name in raw.split(",") if name.strip())


def _env_tz(key: str, default: str = "UTC") -> tzinfo:
    """Resolve an IANA tz name to a tzinfo. Falls back to UTC on anything
    invalid or unavailable (e.g., tzdata missing in slim Docker images)."""
    name = (os.environ.get(key) or default).strip() or default
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        log.warning("invalid/unavailable timezone %r in %s; using UTC", name, key)
        return UTC


@dataclass(frozen=True)
class Config:
    db_path: Path
    log_path: Path
    rate_limit_floor: int
    user_agent: str
    github_token_env: str
    secret_key: str
    secret_key_from_env: bool
    secure_cookie: bool
    forwarded_allow_ips: str
    exclude_repos: frozenset[str]
    tiles_per_row: int
    display_tz: tzinfo


def load() -> Config:
    root = Path.cwd()
    default_data = root / "data"
    env_secret = os.environ.get("GITHUB_TRAFFIC_VAULT_SECRET_KEY", "")
    return Config(
        db_path=Path(_env("GITHUB_TRAFFIC_VAULT_DB", str(default_data / "github-traffic-vault.db"))),
        log_path=Path(_env("GITHUB_TRAFFIC_VAULT_LOG", str(default_data / "github-traffic-vault.log"))),
        rate_limit_floor=_env_int("GITHUB_TRAFFIC_VAULT_RATE_FLOOR", 100),
        user_agent=_env("GITHUB_TRAFFIC_VAULT_UA", "github-traffic-vault/0.1 (+local)"),
        github_token_env="GITHUB_TOKEN",
        secret_key=env_secret or secrets.token_urlsafe(32),
        secret_key_from_env=bool(env_secret),
        secure_cookie=_env_bool("GITHUB_TRAFFIC_VAULT_SECURE_COOKIE", False),
        forwarded_allow_ips=_env("GITHUB_TRAFFIC_VAULT_FORWARDED_IPS", "127.0.0.1"),
        exclude_repos=_env_set("GITHUB_TRAFFIC_VAULT_EXCLUDE_REPOS"),
        tiles_per_row=max(1, min(5, _env_int("GITHUB_TRAFFIC_VAULT_TILES_PER_ROW", 5))),
        display_tz=_env_tz("GITHUB_TRAFFIC_VAULT_DISPLAY_TZ", "UTC"),
    )


def ensure_data_dir(config: Config) -> None:
    config.db_path.parent.mkdir(parents=True, exist_ok=True)
    config.log_path.parent.mkdir(parents=True, exist_ok=True)
