from __future__ import annotations

import logging
import re
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

_GIT_TIMEOUT = 8.0
_FIND_MAX_DEPTH = 5
_GITHUB_REMOTE_RE = re.compile(
    r"(?:github\.com[:/]|github\.com/)(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+?)(?:\.git)?/?$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class BrowseEntry:
    name: str
    path: str
    is_dir: bool
    is_git: bool


@dataclass(frozen=True)
class BrowseListing:
    path: str | None
    parent: str | None
    entries: list[BrowseEntry]
    error: str | None = None


@dataclass(frozen=True)
class LocalGitStatus:
    """Live local checkout status for the detail status card."""

    path: str | None
    linked: bool
    available: bool
    branch: str | None = None
    dirty: bool = False
    dirty_count: int = 0
    ahead: int | None = None
    behind: int | None = None
    has_upstream: bool = False
    remote_url: str | None = None
    remote_full_name: str | None = None
    remote_matches: bool | None = None
    error: str | None = None

    @property
    def worktree_label(self) -> str:
        if not self.available:
            return "unavailable"
        if self.dirty:
            n = self.dirty_count
            return f"Dirty ({n})" if n else "Dirty"
        return "Clean"

    @property
    def worktree_class(self) -> str:
        if not self.available:
            return "other"
        return "fail" if self.dirty else "pass"

    @property
    def upstream_label(self) -> str:
        """Branch tracking status vs @{upstream} (not the GitHub remote URL)."""
        if not self.available:
            return "unavailable"
        if not self.has_upstream:
            return "No upstream"
        ahead = self.ahead or 0
        behind = self.behind or 0
        if ahead == 0 and behind == 0:
            return "Up to date"
        parts: list[str] = []
        if ahead:
            parts.append(f"↑{ahead}")
        if behind:
            parts.append(f"↓{behind}")
        return " ".join(parts)

    @property
    def upstream_class(self) -> str:
        if not self.available or not self.has_upstream:
            return "other"
        ahead = self.ahead or 0
        behind = self.behind or 0
        if ahead or behind:
            return "run"
        return "pass"

    @property
    def origin_label(self) -> str:
        """Whether git remote URL matches the detail-page GitHub repo."""
        if not self.available:
            return "unavailable"
        if self.remote_matches is True:
            return "Matches"
        if self.remote_matches is False:
            return "Mismatch"
        return "Unknown"

    @property
    def origin_class(self) -> str:
        if not self.available:
            return "other"
        if self.remote_matches is True:
            return "pass"
        if self.remote_matches is False:
            return "fail"
        return "other"


def resolve_under_roots(raw: str | Path, roots: Sequence[Path]) -> Path | None:
    """Return resolved absolute path if it lies under a configured root."""
    if not roots:
        return None
    try:
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            return None
        resolved = candidate.resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return None
    for root in roots:
        try:
            root_r = root.expanduser().resolve(strict=False)
        except (OSError, RuntimeError, ValueError):
            continue
        if resolved == root_r or root_r in resolved.parents:
            return resolved
    return None


def list_browse(path: str | None, roots: Sequence[Path]) -> BrowseListing:
    """List directories under a root (or list roots when path is empty)."""
    if not roots:
        return BrowseListing(path=None, parent=None, entries=[], error="no local.roots configured")

    if not path:
        entries = [
            BrowseEntry(
                name=str(root),
                path=str(root.expanduser().resolve(strict=False)),
                is_dir=True,
                is_git=_is_git_dir(root.expanduser()),
            )
            for root in roots
            if root.expanduser().exists()
        ]
        return BrowseListing(path=None, parent=None, entries=entries)

    resolved = resolve_under_roots(path, roots)
    if resolved is None:
        return BrowseListing(path=path, parent=None, entries=[], error="path outside allowed roots")
    if not resolved.is_dir():
        return BrowseListing(path=str(resolved), parent=None, entries=[], error="not a directory")

    parent: str | None = None
    for root in roots:
        try:
            root_r = root.expanduser().resolve(strict=False)
        except (OSError, RuntimeError, ValueError):
            continue
        if resolved == root_r:
            parent = None
            break
        if root_r in resolved.parents:
            parent = str(resolved.parent)
            break

    children_out: list[BrowseEntry] = []
    try:
        children = sorted(resolved.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except OSError as exc:
        return BrowseListing(path=str(resolved), parent=parent, entries=[], error=str(exc))

    for child in children:
        if child.name.startswith("."):
            continue
        if not child.is_dir():
            continue
        children_out.append(
            BrowseEntry(
                name=child.name,
                path=str(child.resolve(strict=False)),
                is_dir=True,
                is_git=_is_git_dir(child),
            )
        )
    return BrowseListing(path=str(resolved), parent=parent, entries=children_out)


def parse_github_full_name(remote_url: str) -> str | None:
    """Extract ``owner/repo`` from a GitHub remote URL, if possible."""
    url = remote_url.strip()
    if not url:
        return None
    m = _GITHUB_REMOTE_RE.search(url.replace("\\", "/"))
    if not m:
        return None
    return f"{m.group('owner')}/{m.group('repo')}"


def inspect_local(
    local_path: str | None,
    roots: Sequence[Path],
    expected_full_name: str,
) -> LocalGitStatus:
    """Read branch / dirty / ahead-behind for a linked path."""
    if not local_path:
        return LocalGitStatus(path=None, linked=False, available=False)

    resolved = resolve_under_roots(local_path, roots)
    if resolved is None:
        return LocalGitStatus(
            path=local_path,
            linked=True,
            available=False,
            error="path outside allowed roots",
        )
    if not resolved.exists():
        return LocalGitStatus(
            path=str(resolved),
            linked=True,
            available=False,
            error="path does not exist",
        )
    if not _is_git_dir(resolved):
        return LocalGitStatus(
            path=str(resolved),
            linked=True,
            available=False,
            error="not a git repository",
        )

    branch = _git_stdout(resolved, ["rev-parse", "--abbrev-ref", "HEAD"])
    if branch == "HEAD":
        short = _git_stdout(resolved, ["rev-parse", "--short", "HEAD"])
        branch = f"detached@{short}" if short else "detached"

    porcelain = _git_stdout(resolved, ["status", "--porcelain"])
    dirty_lines = [ln for ln in (porcelain or "").splitlines() if ln.strip()]
    dirty_count = len(dirty_lines)

    remote_url = _git_stdout(resolved, ["remote", "get-url", "origin"]) or _first_remote_url(resolved)
    remote_full_name = parse_github_full_name(remote_url) if remote_url else None
    remote_matches = remote_full_name.lower() == expected_full_name.lower() if remote_full_name else None

    ahead: int | None = None
    behind: int | None = None
    has_upstream = False
    counts = _git_stdout(resolved, ["rev-list", "--left-right", "--count", "@{upstream}...HEAD"])
    if counts is not None:
        parts = counts.split()
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            has_upstream = True
            behind = int(parts[0])
            ahead = int(parts[1])

    return LocalGitStatus(
        path=str(resolved),
        linked=True,
        available=True,
        branch=branch,
        dirty=dirty_count > 0,
        dirty_count=dirty_count,
        ahead=ahead,
        behind=behind,
        has_upstream=has_upstream,
        remote_url=remote_url,
        remote_full_name=remote_full_name,
        remote_matches=remote_matches,
    )


def validate_link_path(
    raw_path: str,
    roots: Sequence[Path],
    expected_full_name: str,
) -> tuple[Path, str | None]:
    """Validate a path for linking. Returns (resolved_path, error)."""
    if not roots:
        return Path(), "configure local.roots in config.yaml (Settings → Local)"
    raw = raw_path.strip()
    if not raw:
        return Path(), "path is required"
    resolved = resolve_under_roots(raw, roots)
    if resolved is None:
        return Path(), "path must be under a configured local root"
    if not resolved.is_dir():
        return Path(), "path is not a directory"
    if not _is_git_dir(resolved):
        return Path(), "path is not a git repository"
    status = inspect_local(str(resolved), roots, expected_full_name)
    if status.remote_matches is False:
        got = status.remote_full_name or "unknown"
        return Path(), f"remote is {got}, expected {expected_full_name}"
    if status.remote_matches is None:
        return Path(), "could not determine GitHub remote (set origin to github.com/owner/repo)"
    return resolved, None


def find_under_roots(roots: Sequence[Path], full_name: str) -> list[str]:
    """Scan roots for git repos whose origin matches ``full_name``."""
    matches: list[str] = []
    want = full_name.lower()
    for root in roots:
        try:
            root_r = root.expanduser().resolve(strict=False)
        except (OSError, RuntimeError, ValueError):
            continue
        if not root_r.is_dir():
            continue
        for path in _walk_dirs(root_r, max_depth=_FIND_MAX_DEPTH):
            if not _is_git_dir(path):
                continue
            url = _git_stdout(path, ["remote", "get-url", "origin"]) or _first_remote_url(path)
            if not url:
                continue
            parsed = parse_github_full_name(url)
            if parsed and parsed.lower() == want:
                matches.append(str(path))
    return matches


def _walk_dirs(root: Path, *, max_depth: int) -> list[Path]:
    found: list[Path] = []

    def _walk(current: Path, depth: int) -> None:
        if depth > max_depth:
            return
        found.append(current)
        if _is_git_dir(current) and depth > 0:
            # do not descend into nested checkouts under a git root
            return
        try:
            children = sorted(current.iterdir(), key=lambda p: p.name.lower())
        except OSError:
            return
        for child in children:
            if not child.is_dir() or child.name.startswith("."):
                continue
            _walk(child, depth + 1)

    _walk(root, 0)
    return found


def _is_git_dir(path: Path) -> bool:
    try:
        if not path.is_dir():
            return False
    except OSError:
        return False
    git = path / ".git"
    return git.is_dir() or git.is_file()  # file = git worktree


def _git_stdout(cwd: Path, args: list[str]) -> str | None:
    # Bind-mounted trees often differ in uid from the container process;
    # safe.directory=* lets git inspect them without rewriting host config.
    try:
        proc = subprocess.run(
            ["git", "-c", "safe.directory=*", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.debug("git %s in %s failed: %s", args, cwd, exc)
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def _first_remote_url(cwd: Path) -> str | None:
    remotes = _git_stdout(cwd, ["remote"])
    if not remotes:
        return None
    first = remotes.splitlines()[0].strip()
    if not first:
        return None
    return _git_stdout(cwd, ["remote", "get-url", first])
