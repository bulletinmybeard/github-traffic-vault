"""Read-only queries shaping the data for the web templates."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from github_traffic_vault.models import (
    ChangeEvent,
    DailyClones,
    DailyViews,
    PathSnapshot,
    ReferrerSnapshot,
    Repo,
    SyncRun,
)

_MONTH_NAMES = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)


@dataclass
class DayRow:
    date: date
    views: int
    v_uniques: int
    clones: int
    c_uniques: int

    @property
    def is_zero(self) -> bool:
        return self.views == 0 and self.clones == 0


@dataclass
class RepoTotal:
    """Per-repo summary, used on the index tile grid."""

    owner: str
    name: str
    full_name: str
    stargazers: int
    is_fork: bool
    is_archived: bool
    is_private: bool
    total_views: int
    total_v_uniques: int
    total_clones: int
    total_c_uniques: int
    today_views: int
    today_v_uniques: int
    today_clones: int
    today_c_uniques: int
    delta_views: str | None = None
    delta_clones: str | None = None
    delta_views_class: str = ""
    delta_clones_class: str = ""
    delta_views_pct: int | None = None
    delta_clones_pct: int | None = None
    sparkline_views: list[int] = field(default_factory=list)
    sparkline_clones: list[int] = field(default_factory=list)
    created_at: datetime | None = None


@dataclass
class ReferrerRow:
    referrer: str
    count: int
    uniques: int


@dataclass
class PathRow:
    path: str
    title: str | None
    count: int
    uniques: int


@dataclass
class RevisionRow:
    recorded_at: datetime
    kind: str
    traffic_date: date
    prev_count: int
    new_count: int
    count_delta: int
    prev_uniques: int
    new_uniques: int
    uniques_delta: int
    sync_run_id: int


@dataclass
class RepoStatus:
    """Repository status snapshot for the detail-page status card."""

    full_name: str
    ci_status: str | None
    ci_conclusion: str | None
    ci_workflow: str | None
    ci_branch: str | None
    ci_run_url: str | None
    ci_run_at: datetime | None
    release_kind: str | None
    release_name: str | None
    release_url: str | None
    release_at: datetime | None
    open_pr_count: int | None
    synced_at: datetime | None

    @property
    def ci_state(self) -> str:
        """Normalized CI state: passing / failing / running / other / none."""
        if self.ci_status is None:
            return "none"
        if self.ci_status != "completed":
            return "running"
        if self.ci_conclusion == "success":
            return "passing"
        if self.ci_conclusion == "failure":
            return "failing"
        return "other"

    @property
    def ci_pill_class(self) -> str:
        return {
            "passing": "pass",
            "failing": "fail",
            "running": "run",
            "other": "other",
            "none": "",
        }[self.ci_state]

    @property
    def ci_label(self) -> str:
        state = self.ci_state
        if state in ("passing", "failing", "running"):
            return state
        return self.ci_conclusion or "unknown"

    @property
    def pulls_url(self) -> str:
        return f"https://github.com/{self.full_name}/pulls"


@dataclass(frozen=True)
class ArchiveSpan:
    """Earliest/latest traffic dates stored in the vault."""

    earliest: date | None
    latest: date | None

    @property
    def label(self) -> str:
        if self.earliest is None or self.latest is None:
            return "no traffic archived yet"
        if self.earliest == self.latest:
            return self.earliest.isoformat()
        return f"{self.earliest.isoformat()} - {self.latest.isoformat()}"

    def range_label(self, *, through: date) -> str | None:
        """Compact ``(start - through)`` label for the period picker."""
        if self.earliest is None:
            return None
        return f"({self.earliest.isoformat()} - {through.isoformat()})"


@dataclass
class Period:
    """Resolved date range driving the detail-page chart + period selector."""

    start: date
    end: date
    kind: str  # "month" | "days" | "all" | "custom"
    days: int  # active preset day-count; 0 when kind != "days"
    label: str
    min_date: date
    max_date: date
    open_end: bool = False

    @property
    def query_string(self) -> str:
        if self.kind == "custom":
            return f"from={self.start.isoformat()}&to={self.end.isoformat()}"
        if self.kind == "all":
            return "range=all"
        if self.kind == "month":
            return "range=month"
        return f"days={self.days}"

    @property
    def short_label(self) -> str:
        """Compact label for index tiles (e.g., 'Jun', '30d')."""
        if self.kind == "month":
            return _MONTH_NAMES[self.end.month - 1]
        if self.kind == "days":
            return f"{self.days}d"
        if self.kind == "all":
            return "all"
        return "range"


@dataclass(frozen=True)
class PeriodPreset:
    """A selectable window in the period dropdown."""

    label: str
    query_string: str
    kind: str  # "month" | "days"
    days: int = 0

    def matches(self, period: Period) -> bool:
        if self.kind == "month":
            return period.kind == "month"
        return period.kind == "days" and period.days == self.days


PERIOD_PRESETS: tuple[PeriodPreset, ...] = (
    PeriodPreset("This month", "range=month", "month"),
    PeriodPreset("Last 7 days", "days=7", "days", 7),
    PeriodPreset("Last 14 days", "days=14", "days", 14),
    PeriodPreset("Last month", "days=30", "days", 30),
    PeriodPreset("Last 2 months", "days=60", "days", 60),
    PeriodPreset("Last 3 months", "days=90", "days", 90),
)


@dataclass
class RepoView:
    owner: str
    name: str
    full_name: str
    stargazers: int
    forks: int
    watchers: int
    is_fork: bool
    is_archived: bool
    is_private: bool
    default_branch: str | None
    created_at: datetime | None
    total_views: int
    total_v_uniques: int
    total_clones: int
    total_c_uniques: int
    status: RepoStatus
    local_path: str | None = None
    delta_views: str | None = None
    delta_clones: str | None = None
    delta_views_class: str = ""
    delta_clones_class: str = ""
    archive: ArchiveSpan | None = None
    period: Period | None = None
    daily: list[DayRow] = field(default_factory=list)  # ascending date order, for the chart
    days_by_month: list[tuple[str, list[DayRow]]] = field(default_factory=list)
    referrers: list[ReferrerRow] = field(default_factory=list)
    paths: list[PathRow] = field(default_factory=list)
    revisions: list[RevisionRow] = field(default_factory=list)

    @property
    def github_url(self) -> str:
        return f"https://github.com/{self.full_name}"


@dataclass
class SyncSummary:
    id: int
    started_at: datetime
    finished_at: datetime | None
    repos_total: int
    repos_ok: int
    repos_err: int
    rate_remaining: int | None
    notes: str | None


def latest_sync(session: Session) -> SyncSummary | None:
    row = session.scalar(select(SyncRun).order_by(SyncRun.id.desc()).limit(1))
    if row is None:
        return None
    return SyncSummary(
        id=row.id,
        started_at=row.started_at,
        finished_at=row.finished_at,
        repos_total=row.repos_total,
        repos_ok=row.repos_ok,
        repos_err=row.repos_err,
        rate_remaining=row.rate_remaining,
        notes=row.notes,
    )


SORT_OPTIONS: tuple[tuple[str, str], ...] = (
    ("created", "Newest"),
    ("clones", "Clones"),
    ("views", "Views"),
    ("stars", "Stars"),
    ("name", "Name"),
    ("delta_views", "View trend"),
    ("delta_clones", "Clone trend"),
)

FILTER_OPTIONS: tuple[tuple[str, str], ...] = (
    ("public", "Public"),
    ("private", "Private"),
    ("archived", "Archived"),
    ("fork", "Forks"),
    ("all", "All"),
)

_FILTER_LABELS = dict(FILTER_OPTIONS)


def filter_counts(
    session: Session,
    *,
    exclude_repos: frozenset[str] | None = None,
) -> dict[str, int]:
    """Per-filter repo counts (vault inventory, not period traffic!)."""
    repos = list(session.scalars(select(Repo)).all())
    if exclude_repos:
        repos = [r for r in repos if r.name.lower() not in exclude_repos]
    return {
        "all": len(repos),
        "public": sum(1 for r in repos if not r.is_private),
        "private": sum(1 for r in repos if r.is_private),
        "archived": sum(1 for r in repos if r.is_archived),
        "fork": sum(1 for r in repos if r.is_fork),
    }


def build_filter_options(counts: dict[str, int]) -> list[dict[str, str | int | bool]]:
    """Filter chips in display order, with counts and disabled state."""
    keys = ["public", "private", "fork", "all", "archived"]

    options: list[dict[str, str | int | bool]] = []
    for key in keys:
        count = counts.get(key, 0)
        disabled = key != "all" and count == 0
        options.append(
            {
                "key": key,
                "label": f"{_FILTER_LABELS[key]} ({count})",
                "count": count,
                "disabled": disabled,
            }
        )
    return options


def index_query_string(period: Period, sort: str = "created", filter_: str = "public") -> str:
    parts = [period.query_string]
    if sort != "created":
        parts.append(f"sort={sort}")
    if filter_ != "public":
        parts.append(f"filter={filter_}")
    return "&".join(parts)


def prior_period_window(period: Period) -> tuple[date, date] | None:
    if period.kind == "all":
        return None
    length = (period.end - period.start).days + 1
    if period.kind == "month":
        prior_month_last = period.start - timedelta(days=1)
        prior_start = prior_month_last.replace(day=1)
        prior_end = prior_start + timedelta(days=length - 1)
        if prior_end > prior_month_last:
            prior_end = prior_month_last
        return prior_start, prior_end
    prior_end = period.start - timedelta(days=1)
    prior_start = prior_end - timedelta(days=length - 1)
    return prior_start, prior_end


def delta_label(current: int, prior: int, *, comparable: bool = True) -> str | None:
    """Period-over-period badge for index/detail tiles."""
    if not comparable:
        return None
    if current == 0:
        return None
    if prior == 0:
        return "new"
    pct = round((current - prior) / prior * 100)
    if pct == 0:
        return None
    return f"{pct:+d}%"


def delta_class(label: str | None) -> str:
    if label is None:
        return ""
    if label == "new":
        return "new"
    if label.startswith("+"):
        return "up"
    if label.startswith("-"):
        return "down"
    return "up"


def delta_pct(current: int, prior: int, *, comparable: bool = True) -> int | None:
    if not comparable or current == 0 or prior == 0:
        return None
    return round((current - prior) / prior * 100)


def _repo_period_totals(
    repo_ids: set[int],
    views: dict[tuple[int, date], DailyViews],
    clones: dict[tuple[int, date], DailyClones],
) -> dict[int, tuple[int, int, int, int]]:
    out: dict[int, tuple[int, int, int, int]] = {rid: (0, 0, 0, 0) for rid in repo_ids}
    for (rid, _d), v in views.items():
        if rid in out:
            t = out[rid]
            out[rid] = (t[0] + v.count, t[1] + v.uniques, t[2], t[3])
    for (rid, _d), c in clones.items():
        if rid in out:
            t = out[rid]
            out[rid] = (t[0], t[1], t[2] + c.count, t[3] + c.uniques)
    return out


def _daily_date_filters(
    model: type[DailyViews] | type[DailyClones],
    *,
    start: date,
    end: date,
    open_end: bool,
) -> list[ColumnElement[bool]]:
    clauses = [model.date >= start]
    if not open_end:
        clauses.append(model.date <= end)
    return clauses


def _date_range(start: date, end: date) -> list[date]:
    length = (end - start).days + 1
    return [start + timedelta(days=i) for i in range(length)]


def _sparkline_window(
    period: Period | None,
    *,
    traffic_today: date,
    sparkline_days: int,
) -> tuple[date, date, bool]:
    """Return sparkline start/end and whether to reuse the period traffic query."""
    if period is not None and period.kind != "all":
        return period.start, period.end, True
    spark_end = traffic_today
    spark_start = traffic_today - timedelta(days=sparkline_days - 1)
    return spark_start, spark_end, False


def _daily_count_series(
    repo_id: int,
    dates: list[date],
    rows: dict[tuple[int, date], DailyViews] | dict[tuple[int, date], DailyClones],
) -> list[int]:
    return [rows[(repo_id, d)].count if (repo_id, d) in rows else 0 for d in dates]


def repo_totals(
    session: Session,
    *,
    start: date,
    end: date,
    traffic_today: date,
    open_end: bool = False,
    exclude_repos: frozenset[str] | None = None,
    sort: str = "created",
    filter_: str = "public",
    sparkline_days: int = 14,
    period: Period | None = None,
    include_today: bool = True,
    include_sparklines: bool = True,
) -> list[RepoTotal]:
    """Per-repo totals over ``start``..``end`` inclusive, with optional sort/filter."""

    repos = list(
        session.scalars(
            select(Repo).order_by(Repo.created_at.is_(None), Repo.created_at.desc(), Repo.full_name)
        ).all()
    )
    if exclude_repos:
        repos = [r for r in repos if r.name.lower() not in exclude_repos]

    views = {
        (r.repo_id, r.date): r
        for r in session.scalars(
            select(DailyViews).where(
                *_daily_date_filters(DailyViews, start=start, end=end, open_end=open_end)
            )
        ).all()
    }
    clones = {
        (r.repo_id, r.date): r
        for r in session.scalars(
            select(DailyClones).where(
                *_daily_date_filters(DailyClones, start=start, end=end, open_end=open_end)
            )
        ).all()
    }
    prior_totals: dict[int, tuple[int, int, int, int]] = {}
    deltas_comparable = False
    if period is not None:
        prior_win = prior_period_window(period)
        if prior_win is not None:
            deltas_comparable = True
            p_start, p_end = prior_win
            prior_views = {
                (r.repo_id, r.date): r
                for r in session.scalars(
                    select(DailyViews).where(
                        *_daily_date_filters(DailyViews, start=p_start, end=p_end, open_end=False)
                    )
                ).all()
            }
            prior_clones = {
                (r.repo_id, r.date): r
                for r in session.scalars(
                    select(DailyClones).where(
                        *_daily_date_filters(DailyClones, start=p_start, end=p_end, open_end=False)
                    )
                ).all()
            }
            prior_totals = _repo_period_totals({r.id for r in repos}, prior_views, prior_clones)

    spark_dates: list[date] = []
    reuse_period_sparklines = False
    alltime_views: dict[tuple[int, date], DailyViews] = {}
    alltime_clones: dict[tuple[int, date], DailyClones] = {}
    if include_sparklines:
        spark_start, spark_end, reuse_period_sparklines = _sparkline_window(
            period, traffic_today=traffic_today, sparkline_days=sparkline_days
        )
        spark_dates = _date_range(spark_start, spark_end)
        if not reuse_period_sparklines:
            alltime_views = {
                (r.repo_id, r.date): r
                for r in session.scalars(
                    select(DailyViews).where(DailyViews.date >= spark_start, DailyViews.date <= spark_end)
                ).all()
            }
            alltime_clones = {
                (r.repo_id, r.date): r
                for r in session.scalars(
                    select(DailyClones).where(DailyClones.date >= spark_start, DailyClones.date <= spark_end)
                ).all()
            }

    today_views: dict[int, DailyViews] = {}
    today_clones: dict[int, DailyClones] = {}
    if include_today:
        today_views = {
            r.repo_id: r
            for r in session.scalars(select(DailyViews).where(DailyViews.date == traffic_today)).all()
        }
        today_clones = {
            r.repo_id: r
            for r in session.scalars(select(DailyClones).where(DailyClones.date == traffic_today)).all()
        }

    repo_ids = {r.id for r in repos}
    current_totals = _repo_period_totals(repo_ids, views, clones)

    out: list[RepoTotal] = []
    for repo in repos:
        totals = current_totals.get(repo.id, (0, 0, 0, 0))
        prior = prior_totals.get(repo.id, (0, 0, 0, 0))
        dv = delta_label(totals[0], prior[0], comparable=deltas_comparable)
        dc = delta_label(totals[2], prior[2], comparable=deltas_comparable)
        if include_sparklines:
            if reuse_period_sparklines:
                spark_views = _daily_count_series(repo.id, spark_dates, views)
                spark_clones = _daily_count_series(repo.id, spark_dates, clones)
            else:
                spark_views = _daily_count_series(repo.id, spark_dates, alltime_views)
                spark_clones = _daily_count_series(repo.id, spark_dates, alltime_clones)
        else:
            spark_views = []
            spark_clones = []
        today_v = today_views.get(repo.id)
        today_c = today_clones.get(repo.id)
        out.append(
            RepoTotal(
                owner=repo.owner,
                name=repo.name,
                full_name=repo.full_name,
                stargazers=repo.stargazers,
                is_fork=repo.is_fork,
                is_archived=repo.is_archived,
                is_private=repo.is_private,
                total_views=totals[0],
                total_v_uniques=totals[1],
                total_clones=totals[2],
                total_c_uniques=totals[3],
                today_views=today_v.count if today_v else 0,
                today_v_uniques=today_v.uniques if today_v else 0,
                today_clones=today_c.count if today_c else 0,
                today_c_uniques=today_c.uniques if today_c else 0,
                delta_views=dv,
                delta_clones=dc,
                delta_views_class=delta_class(dv),
                delta_clones_class=delta_class(dc),
                delta_views_pct=delta_pct(totals[0], prior[0], comparable=deltas_comparable),
                delta_clones_pct=delta_pct(totals[2], prior[2], comparable=deltas_comparable),
                sparkline_views=spark_views,
                sparkline_clones=spark_clones,
                created_at=repo.created_at,
            )
        )

    if filter_ == "public":
        out = [t for t in out if not t.is_private]
    elif filter_ == "private":
        out = [t for t in out if t.is_private]
    elif filter_ == "archived":
        out = [t for t in out if t.is_archived]
    elif filter_ == "fork":
        out = [t for t in out if t.is_fork]

    def _sort_key(t: RepoTotal) -> tuple[str | int | float, ...]:
        if sort == "views":
            return (-t.total_views, t.full_name)
        if sort == "clones":
            return (-t.total_clones, t.full_name)
        if sort == "stars":
            return (-t.stargazers, t.full_name)
        if sort == "created":
            ts = t.created_at.timestamp() if t.created_at else 0.0
            return (-ts, t.full_name)
        if sort == "delta_views":
            return (-(t.delta_views_pct if t.delta_views_pct is not None else -(10**9)), t.full_name)
        if sort == "delta_clones":
            return (-(t.delta_clones_pct if t.delta_clones_pct is not None else -(10**9)), t.full_name)
        return (t.full_name,)

    out.sort(key=_sort_key)
    return out


def repo_detail(
    session: Session,
    full_name: str,
    *,
    today: date,
    days: int | None = None,
    range_: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    exclude_repos: frozenset[str] | None = None,
) -> RepoView | None:
    """Single repo: daily breakdown over the resolved period, grouped by month."""
    repo = session.scalar(select(Repo).where(Repo.full_name == full_name))
    if repo is not None and exclude_repos and repo.name.lower() in exclude_repos:
        return None
    if repo is None:
        return None

    period = resolve_period(
        days=days,
        range_=range_,
        date_from=date_from,
        date_to=date_to,
        earliest=earliest_data_date(session, repo.id),
        today=today,
    )

    v_filters = [
        DailyViews.repo_id == repo.id,
        *_daily_date_filters(DailyViews, start=period.start, end=period.end, open_end=period.open_end),
    ]
    c_filters = [
        DailyClones.repo_id == repo.id,
        *_daily_date_filters(DailyClones, start=period.start, end=period.end, open_end=period.open_end),
    ]
    v_rows = {r.date: r for r in session.scalars(select(DailyViews).where(*v_filters)).all()}
    c_rows = {r.date: r for r in session.scalars(select(DailyClones).where(*c_filters)).all()}
    # Ascending date order is what the chart wants; descending is what
    # the month-grouped table wants. Build asc once, reverse once.
    asc_dates = sorted(set(v_rows) | set(c_rows))

    daily: list[DayRow] = []
    totals = [0, 0, 0, 0]
    for d in asc_dates:
        v = v_rows.get(d)
        c = c_rows.get(d)
        v_count = v.count if v else 0
        v_uniq = v.uniques if v else 0
        c_count = c.count if c else 0
        c_uniq = c.uniques if c else 0
        daily.append(DayRow(date=d, views=v_count, v_uniques=v_uniq, clones=c_count, c_uniques=c_uniq))
        totals[0] += v_count
        totals[1] += v_uniq
        totals[2] += c_count
        totals[3] += c_uniq

    prior_win = prior_period_window(period)
    deltas_comparable = prior_win is not None
    prior_views_total = 0
    prior_clones_total = 0
    if prior_win is not None:
        p_start, p_end = prior_win
        prior_v = session.scalars(
            select(DailyViews).where(
                DailyViews.repo_id == repo.id,
                *_daily_date_filters(DailyViews, start=p_start, end=p_end, open_end=False),
            )
        ).all()
        prior_c = session.scalars(
            select(DailyClones).where(
                DailyClones.repo_id == repo.id,
                *_daily_date_filters(DailyClones, start=p_start, end=p_end, open_end=False),
            )
        ).all()
        prior_views_total = sum(r.count for r in prior_v)
        prior_clones_total = sum(r.count for r in prior_c)

    dv = delta_label(totals[0], prior_views_total, comparable=deltas_comparable)
    dc = delta_label(totals[2], prior_clones_total, comparable=deltas_comparable)

    return RepoView(
        owner=repo.owner,
        name=repo.name,
        full_name=repo.full_name,
        stargazers=repo.stargazers,
        forks=repo.forks,
        watchers=repo.watchers,
        is_fork=repo.is_fork,
        is_archived=repo.is_archived,
        is_private=repo.is_private,
        default_branch=repo.default_branch,
        created_at=repo.created_at,
        total_views=totals[0],
        total_v_uniques=totals[1],
        total_clones=totals[2],
        total_c_uniques=totals[3],
        local_path=repo.local_path,
        delta_views=dv,
        delta_clones=dc,
        delta_views_class=delta_class(dv),
        delta_clones_class=delta_class(dc),
        status=_repo_status(repo),
        archive=archive_span_for_repo(session, repo.id),
        period=period,
        daily=daily,
        days_by_month=_group_by_month(list(reversed(daily))),
        referrers=latest_referrers(session, repo.id),
        paths=latest_paths(session, repo.id),
        revisions=revision_log(session, repo.id),
    )


def repo_views(
    session: Session,
    *,
    start: date,
    end: date,
    open_end: bool = False,
    exclude_repos: frozenset[str] | None = None,
) -> list[RepoView]:
    """One RepoView per repo, with traffic over ``start``..``end`` inclusive.

    Kept for the JSON API / backward compat. Index page uses
    `repo_totals` now.
    """
    repos = session.scalars(
        select(Repo).order_by(Repo.created_at.is_(None), Repo.created_at.desc(), Repo.full_name)
    ).all()
    if exclude_repos:
        repos = [r for r in repos if r.name.lower() not in exclude_repos]
    views = {
        (r.repo_id, r.date): r
        for r in session.scalars(
            select(DailyViews).where(
                *_daily_date_filters(DailyViews, start=start, end=end, open_end=open_end)
            )
        ).all()
    }
    clones = {
        (r.repo_id, r.date): r
        for r in session.scalars(
            select(DailyClones).where(
                *_daily_date_filters(DailyClones, start=start, end=end, open_end=open_end)
            )
        ).all()
    }

    out: list[RepoView] = []
    for repo in repos:
        dates = sorted(
            {d for (rid, d) in views if rid == repo.id} | {d for (rid, d) in clones if rid == repo.id},
            reverse=True,
        )
        day_rows: list[DayRow] = []
        totals = [0, 0, 0, 0]  # views, v_uniques, clones, c_uniques
        for d in dates:
            v = views.get((repo.id, d))
            c = clones.get((repo.id, d))
            v_count = v.count if v else 0
            v_uniq = v.uniques if v else 0
            c_count = c.count if c else 0
            c_uniq = c.uniques if c else 0
            day_rows.append(DayRow(date=d, views=v_count, v_uniques=v_uniq, clones=c_count, c_uniques=c_uniq))
            totals[0] += v_count
            totals[1] += v_uniq
            totals[2] += c_count
            totals[3] += c_uniq

        out.append(
            RepoView(
                owner=repo.owner,
                name=repo.name,
                full_name=repo.full_name,
                stargazers=repo.stargazers,
                forks=repo.forks,
                watchers=repo.watchers,
                is_fork=repo.is_fork,
                is_archived=repo.is_archived,
                is_private=repo.is_private,
                default_branch=repo.default_branch,
                created_at=repo.created_at,
                total_views=totals[0],
                total_v_uniques=totals[1],
                total_clones=totals[2],
                total_c_uniques=totals[3],
                status=_repo_status(repo),
                days_by_month=_group_by_month(day_rows),
            )
        )
    return out


def _group_by_month(days: list[DayRow]) -> list[tuple[str, list[DayRow]]]:
    """Preserves input order (which is descending by date)."""
    groups: list[tuple[str, list[DayRow]]] = []
    for row in days:
        label = f"{_MONTH_NAMES[row.date.month - 1]} {row.date.year}"
        if groups and groups[-1][0] == label:
            groups[-1][1].append(row)
        else:
            groups.append((label, [row]))
    return groups


def _repo_status(repo: Repo) -> RepoStatus:
    return RepoStatus(
        full_name=repo.full_name,
        ci_status=repo.ci_status,
        ci_conclusion=repo.ci_conclusion,
        ci_workflow=repo.ci_workflow,
        ci_branch=repo.ci_branch,
        ci_run_url=repo.ci_run_url,
        ci_run_at=repo.ci_run_at,
        release_kind=repo.release_kind,
        release_name=repo.release_name,
        release_url=repo.release_url,
        release_at=repo.release_at,
        open_pr_count=repo.open_pr_count,
        synced_at=repo.last_synced_at,
    )


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _range_label(start: date, end: date) -> str:
    s = f"{_MONTH_NAMES[start.month - 1]} {start.day}"
    e = f"{_MONTH_NAMES[end.month - 1]} {end.day}, {end.year}"
    if start.year != end.year:
        s = f"{s}, {start.year}"
    return f"{s} - {e}"


def resolve_period(
    *,
    days: int | None,
    range_: str | None,
    date_from: str | None,
    date_to: str | None,
    earliest: date | None,
    today: date,
) -> Period:
    """Resolve the request params into a concrete date range.

    Precedence: a valid custom from/to range wins; then ``range=all``;
    then ``range=month`` (calendar month-to-date); then a positive
    ``days`` window; otherwise a rolling 30-day window (the default).

    ``today`` should be the display-timezone calendar date (see
    ``timefmt.today_in_tz``), not ``datetime.now(UTC).date()``.
    """
    min_date = earliest or today
    start = _parse_date(date_from)
    end = _parse_date(date_to)
    if start is not None and end is not None:
        if start > end:
            start, end = end, start
        return Period(
            start=start,
            end=end,
            kind="custom",
            days=0,
            label=_range_label(start, end),
            min_date=min_date,
            max_date=today,
        )
    if range_ == "all":
        return Period(
            start=earliest or today,
            end=today,
            kind="all",
            days=0,
            label="All time",
            min_date=min_date,
            max_date=today,
            open_end=True,
        )
    if range_ == "month":
        month_start = today.replace(day=1)
        return Period(
            start=month_start,
            end=today,
            kind="month",
            days=0,
            label=_range_label(month_start, today),
            min_date=min_date,
            max_date=today,
        )
    d = 30 if days is None else (days if days > 0 else 30)
    preset = next((p for p in PERIOD_PRESETS if p.kind == "days" and p.days == d), None)
    label = preset.label if preset else f"Last {d} days"
    return Period(
        start=today - timedelta(days=d - 1),
        end=today,
        kind="days",
        days=d,
        label=label,
        min_date=min_date,
        max_date=today,
    )


def earliest_data_date_global(session: Session) -> date | None:
    """Earliest archived traffic date across all repos."""
    v = session.scalar(select(func.min(DailyViews.date)))
    c = session.scalar(select(func.min(DailyClones.date)))
    candidates = [d for d in (v, c) if d is not None]
    return min(candidates) if candidates else None


def latest_data_date_global(session: Session) -> date | None:
    """Latest archived traffic date across all repos."""
    v = session.scalar(select(func.max(DailyViews.date)))
    c = session.scalar(select(func.max(DailyClones.date)))
    candidates = [d for d in (v, c) if d is not None]
    return max(candidates) if candidates else None


def archive_span_global(session: Session) -> ArchiveSpan:
    return ArchiveSpan(earliest=earliest_data_date_global(session), latest=latest_data_date_global(session))


def latest_data_date(session: Session, repo_id: int) -> date | None:
    """Latest archived traffic date for a repo, across views and clones."""
    v = session.scalar(select(func.max(DailyViews.date)).where(DailyViews.repo_id == repo_id))
    c = session.scalar(select(func.max(DailyClones.date)).where(DailyClones.repo_id == repo_id))
    candidates = [d for d in (v, c) if d is not None]
    return max(candidates) if candidates else None


def archive_span_for_repo(session: Session, repo_id: int) -> ArchiveSpan:
    return ArchiveSpan(
        earliest=earliest_data_date(session, repo_id), latest=latest_data_date(session, repo_id)
    )


def earliest_data_date(session: Session, repo_id: int) -> date | None:
    """Earliest archived traffic date for a repo, across views and clones."""
    v = session.scalar(select(func.min(DailyViews.date)).where(DailyViews.repo_id == repo_id))
    c = session.scalar(select(func.min(DailyClones.date)).where(DailyClones.repo_id == repo_id))
    candidates = [d for d in (v, c) if d is not None]
    return min(candidates) if candidates else None


def latest_referrers(session: Session, repo_id: int) -> list[ReferrerRow]:
    run_id = session.scalar(
        select(ReferrerSnapshot.sync_run_id)
        .where(ReferrerSnapshot.repo_id == repo_id)
        .order_by(ReferrerSnapshot.sync_run_id.desc())
        .limit(1)
    )
    if run_id is None:
        return []
    rows = session.scalars(
        select(ReferrerSnapshot)
        .where(ReferrerSnapshot.repo_id == repo_id, ReferrerSnapshot.sync_run_id == run_id)
        .order_by(ReferrerSnapshot.count.desc())
    ).all()
    return [ReferrerRow(referrer=r.referrer, count=r.count, uniques=r.uniques) for r in rows]


def latest_paths(session: Session, repo_id: int) -> list[PathRow]:
    run_id = session.scalar(
        select(PathSnapshot.sync_run_id)
        .where(PathSnapshot.repo_id == repo_id)
        .order_by(PathSnapshot.sync_run_id.desc())
        .limit(1)
    )
    if run_id is None:
        return []
    rows = session.scalars(
        select(PathSnapshot)
        .where(PathSnapshot.repo_id == repo_id, PathSnapshot.sync_run_id == run_id)
        .order_by(PathSnapshot.count.desc())
    ).all()
    return [PathRow(path=r.path, title=r.title, count=r.count, uniques=r.uniques) for r in rows]


def revision_log(session: Session, repo_id: int, *, limit: int = 50) -> list[RevisionRow]:
    rows = session.scalars(
        select(ChangeEvent)
        .where(ChangeEvent.repo_id == repo_id, ChangeEvent.prev_count.is_not(None))
        .order_by(ChangeEvent.recorded_at.desc(), ChangeEvent.date.desc())
        .limit(limit)
    ).all()
    return [
        RevisionRow(
            recorded_at=r.recorded_at,
            kind=r.kind,
            traffic_date=r.date,
            prev_count=r.prev_count or 0,
            new_count=r.new_count,
            count_delta=r.count_delta,
            prev_uniques=r.prev_uniques or 0,
            new_uniques=r.new_uniques,
            uniques_delta=r.uniques_delta,
            sync_run_id=r.sync_run_id,
        )
        for r in rows
    ]
