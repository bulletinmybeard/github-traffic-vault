"""GitHub REST client. Auth, retries, rate-limit tracking."""

from __future__ import annotations

import logging
import subprocess
import time
from dataclasses import dataclass
from typing import Any

import httpx

log = logging.getLogger(__name__)

_API_ROOT = "https://api.github.com"
_API_VERSION = "2022-11-28"
_DEFAULT_TIMEOUT = 30.0
_RETRY_ATTEMPTS = 3
_RETRY_BACKOFF = (1.0, 2.0, 4.0)

# REST `GET /pulls` 404s on repos created from ~2026 onward (GitHub-side
# regression). GraphQL serves PR data for those repos correctly and returns
# an exact count with no 100-item page cap.
_OPEN_PR_COUNT_QUERY = """
query($owner: String!, $name: String!) {
  repository(owner: $owner, name: $name) {
    pullRequests(states: OPEN) { totalCount }
  }
}
"""


class GitHubError(RuntimeError):
    """Wraps non-recoverable GitHub API failures."""


class TokenError(GitHubError):
    """Token could not be resolved."""


@dataclass
class FetchResult:
    """Outcome of one HTTP call.

    `payload` is None when the server returned 304 (use cached data).
    """

    payload: Any | None
    etag: str | None
    last_modified: str | None
    not_modified: bool
    status: int


def resolve_token(configured: str = "") -> str:
    """Env var wins; else shell out to `gh auth token`."""
    if configured.strip():
        return configured.strip()
    try:
        out = subprocess.check_output(["gh", "auth", "token"], stderr=subprocess.DEVNULL, text=True)
    except FileNotFoundError as exc:
        raise TokenError("`gh` CLI not installed and auth.github_token not set in config.yaml") from exc
    except subprocess.CalledProcessError as exc:
        raise TokenError("`gh auth token` failed; run `gh auth login`") from exc
    token = out.strip()
    if not token:
        raise TokenError("`gh auth token` returned empty token")
    return token


class GitHubClient:
    """Authenticated REST client. Tracks rate-limit headers across calls."""

    def __init__(self, token: str, user_agent: str) -> None:
        self._client = httpx.Client(
            base_url=_API_ROOT,
            timeout=_DEFAULT_TIMEOUT,
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": _API_VERSION,
                "Authorization": f"Bearer {token}",
                "User-Agent": user_agent,
            },
        )
        self.rate_remaining: int | None = None
        self.rate_limit: int | None = None
        self.rate_reset_at: int | None = None

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> GitHubClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _update_rate(self, resp: httpx.Response) -> None:
        rem = resp.headers.get("X-RateLimit-Remaining")
        lim = resp.headers.get("X-RateLimit-Limit")
        rst = resp.headers.get("X-RateLimit-Reset")
        if rem is not None:
            self.rate_remaining = int(rem)
        if lim is not None:
            self.rate_limit = int(lim)
        if rst is not None:
            self.rate_reset_at = int(rst)

    def _get(
        self,
        path: str,
        params: dict[str, str | int] | None = None,
        etag: str | None = None,
        last_modified: str | None = None,
        not_found_ok: bool = False,
    ) -> FetchResult:
        headers: dict[str, str] = {}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified

        last_exc: Exception | None = None
        for attempt in range(_RETRY_ATTEMPTS):
            try:
                resp = self._client.get(path, params=params, headers=headers)
            except httpx.HTTPError as exc:
                last_exc = exc
                time.sleep(_RETRY_BACKOFF[attempt])
                continue

            self._update_rate(resp)

            if resp.status_code == 304:
                return FetchResult(
                    payload=None,
                    etag=resp.headers.get("ETag", etag),
                    last_modified=resp.headers.get("Last-Modified", last_modified),
                    not_modified=True,
                    status=304,
                )
            if resp.status_code in (429, 500, 502, 503, 504):
                last_exc = GitHubError(f"{resp.status_code} on {path}: {resp.text[:200]}")
                time.sleep(_RETRY_BACKOFF[attempt])
                continue
            if not_found_ok and resp.status_code == 404:
                return FetchResult(
                    payload=None,
                    etag=None,
                    last_modified=None,
                    not_modified=False,
                    status=404,
                )
            if resp.status_code >= 400:
                raise GitHubError(f"{resp.status_code} on {path}: {resp.text[:300]}")

            return FetchResult(
                payload=resp.json(),
                etag=resp.headers.get("ETag"),
                last_modified=resp.headers.get("Last-Modified"),
                not_modified=False,
                status=resp.status_code,
            )

        assert last_exc is not None
        raise GitHubError(f"giving up after {_RETRY_ATTEMPTS} attempts on {path}: {last_exc}")

    def _graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        """POST a GraphQL query and return its `data` object.

        No ETag/conditional support -- GraphQL uses a separate (points-based)
        rate budget, so the REST rate-limit headers are deliberately not read
        off these responses.
        """
        last_exc: Exception | None = None
        for attempt in range(_RETRY_ATTEMPTS):
            try:
                resp = self._client.post("/graphql", json={"query": query, "variables": variables})
            except httpx.HTTPError as exc:
                last_exc = exc
                time.sleep(_RETRY_BACKOFF[attempt])
                continue

            if resp.status_code in (429, 500, 502, 503, 504):
                last_exc = GitHubError(f"{resp.status_code} on /graphql: {resp.text[:200]}")
                time.sleep(_RETRY_BACKOFF[attempt])
                continue
            if resp.status_code >= 400:
                raise GitHubError(f"{resp.status_code} on /graphql: {resp.text[:300]}")

            # GraphQL returns HTTP 200 even on query errors; the body carries them.
            body: dict[str, Any] = resp.json()
            if body.get("errors"):
                raise GitHubError(f"graphql errors: {body['errors']}")
            data = body.get("data")
            if not isinstance(data, dict):
                raise GitHubError(f"graphql returned no data: {resp.text[:300]}")
            return data

        assert last_exc is not None
        raise GitHubError(f"giving up after {_RETRY_ATTEMPTS} attempts on /graphql: {last_exc}")

    def list_owned_repos(self, *, include_private: bool = False) -> list[dict[str, Any]]:
        """Owned repos for the authenticated user."""
        repos: list[dict[str, Any]] = []
        page = 1
        while True:
            params: dict[str, Any] = {
                "affiliation": "owner",
                "per_page": 100,
                "page": page,
            }
            if not include_private:
                params["visibility"] = "public"
            res = self._get("/user/repos", params=params)
            if not isinstance(res.payload, list) or not res.payload:
                break
            repos.extend(res.payload)
            if len(res.payload) < 100:
                break
            page += 1
        return repos

    def fetch_views(self, owner: str, repo: str, *, etag: str | None = None) -> FetchResult:
        return self._get(f"/repos/{owner}/{repo}/traffic/views", etag=etag)

    def fetch_clones(self, owner: str, repo: str, *, etag: str | None = None) -> FetchResult:
        return self._get(f"/repos/{owner}/{repo}/traffic/clones", etag=etag)

    def fetch_referrers(self, owner: str, repo: str, *, etag: str | None = None) -> FetchResult:
        return self._get(f"/repos/{owner}/{repo}/traffic/popular/referrers", etag=etag)

    def fetch_paths(self, owner: str, repo: str, *, etag: str | None = None) -> FetchResult:
        return self._get(f"/repos/{owner}/{repo}/traffic/popular/paths", etag=etag)

    def fetch_latest_run(self, owner: str, repo: str, *, etag: str | None = None) -> FetchResult:
        """Most recent workflow run across all branches and PRs."""
        return self._get(f"/repos/{owner}/{repo}/actions/runs", params={"per_page": 1}, etag=etag)

    def fetch_latest_release(self, owner: str, repo: str, *, etag: str | None = None) -> FetchResult:
        """Latest published Release. Status 404 (no Release) is a normal result."""
        return self._get(f"/repos/{owner}/{repo}/releases/latest", etag=etag, not_found_ok=True)

    def fetch_latest_tag(self, owner: str, repo: str, *, etag: str | None = None) -> FetchResult:
        """Newest git tag. Fallback when a repo has no published Release."""
        return self._get(f"/repos/{owner}/{repo}/tags", params={"per_page": 1}, etag=etag)

    def fetch_open_pr_count(self, owner: str, repo: str) -> int:
        """Exact count of open PRs, via GraphQL (see `_OPEN_PR_COUNT_QUERY`)."""
        data = self._graphql(_OPEN_PR_COUNT_QUERY, {"owner": owner, "name": repo})
        repo_node = data.get("repository")
        if repo_node is None:
            raise GitHubError(f"repository {owner}/{repo} not found via GraphQL")
        return int(repo_node["pullRequests"]["totalCount"])
