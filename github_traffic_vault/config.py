"""Resolved configuration. Env vars > built-in defaults."""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path


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


@dataclass(frozen=True)
class Config:
    db_path: Path
    log_path: Path
    rate_limit_floor: int
    user_agent: str
    github_token_env: str
    secret_key: str
    secret_key_from_env: bool


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
    )


def ensure_data_dir(config: Config) -> None:
    config.db_path.parent.mkdir(parents=True, exist_ok=True)
    config.log_path.parent.mkdir(parents=True, exist_ok=True)
