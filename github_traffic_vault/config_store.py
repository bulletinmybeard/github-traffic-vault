"""Read/write ``config.yaml``. Settings UI updates a fixed allowlist of keys."""

from __future__ import annotations

import logging
import os
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger(__name__)

DEFAULT_CONFIG: dict[str, Any] = {
    "auth": {
        "github_token": "",
        "secret_key": "",
    },
    "display": {
        "timezone": "UTC",
        "tiles_per_row": 5,
    },
    "cards": {
        "show_today": True,
        "show_sparklines": True,
        "sparklines_compact": False,
        "sparkline_days": 14,
    },
    "sync": {
        "include_private": False,
        "exclude_repos": [],
    },
    "paths": {
        "db": "data/github-traffic-vault.db",
        "log": "data/github-traffic-vault.log",
    },
    "server": {
        "rate_limit_floor": 100,
        "user_agent": "github-traffic-vault/0.1 (+local)",
        "secure_cookie": False,
        "forwarded_allow_ips": "*",
    },
}

SETTINGS_SECTIONS: dict[str, frozenset[str]] = {
    "display": frozenset({"timezone", "tiles_per_row"}),
    "cards": frozenset({"show_today", "show_sparklines", "sparklines_compact", "sparkline_days"}),
    "sync": frozenset({"include_private", "exclude_repos"}),
}


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    out = deepcopy(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def read_config_file(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return deepcopy(DEFAULT_CONFIG)
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError(f"config root must be a mapping: {path}")
    return _deep_merge(DEFAULT_CONFIG, loaded)


def write_config_file(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    header = "# github-traffic-vault configuration\n"
    body = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, default_flow_style=False)
    content = header + body
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    try:
        os.replace(tmp, path)
    except OSError:
        # Bind-mounted files can't always be atomically replaced
        # across devices; write in place instead.
        tmp.unlink(missing_ok=True)
        path.write_text(content, encoding="utf-8")


def _validate_settings_patch(patch: dict[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for section, fields in patch.items():
        if section not in SETTINGS_SECTIONS:
            raise ValueError(f"settings section not allowed: {section}")
        if not isinstance(fields, dict):
            raise ValueError(f"settings section must be a mapping: {section}")
        allowed = SETTINGS_SECTIONS[section]
        bad = set(fields) - allowed
        if bad:
            raise ValueError(f"keys not allowed in {section}: {sorted(bad)}")
        clean[section] = fields
    return clean


def update_config_file(path: Path, patch: dict[str, Any]) -> list[str]:
    """Merge a settings patch into ``config.yaml``. Returns changed dotted keys."""
    patch = _validate_settings_patch(patch)
    current = read_config_file(path)
    changed: list[str] = []

    for section, fields in patch.items():
        bucket = current.setdefault(section, {})
        for key, value in fields.items():
            old = bucket.get(key)
            if old != value:
                changed.append(f"{section}.{key}")
            bucket[key] = value

    write_config_file(path, current)
    return changed
