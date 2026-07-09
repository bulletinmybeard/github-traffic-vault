"""SQLAlchemy engine + session factory. SQLite with WAL mode."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine, create_engine, event, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from github_traffic_vault.models import Base


def make_engine(db_path: Path) -> Engine:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f"sqlite:///{db_path}",
        echo=False,
        future=True,
        connect_args={"check_same_thread": False, "timeout": 30},
    )

    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_conn, _record):  # type: ignore[no-untyped-def]
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    return engine


def init_schema(engine: Engine) -> None:
    Base.metadata.create_all(engine)
    _apply_ad_hoc_migrations(engine)


_AD_HOC_COLUMNS: tuple[tuple[str, str, str], ...] = (
    # (table, column, full DDL statement) -- statements are literal, not templated
    ("repos", "created_at", "ALTER TABLE repos ADD COLUMN created_at DATETIME"),
    ("repos", "ci_status", "ALTER TABLE repos ADD COLUMN ci_status VARCHAR"),
    ("repos", "ci_conclusion", "ALTER TABLE repos ADD COLUMN ci_conclusion VARCHAR"),
    ("repos", "ci_workflow", "ALTER TABLE repos ADD COLUMN ci_workflow VARCHAR"),
    ("repos", "ci_branch", "ALTER TABLE repos ADD COLUMN ci_branch VARCHAR"),
    ("repos", "ci_run_url", "ALTER TABLE repos ADD COLUMN ci_run_url VARCHAR"),
    ("repos", "ci_run_at", "ALTER TABLE repos ADD COLUMN ci_run_at DATETIME"),
    ("repos", "release_kind", "ALTER TABLE repos ADD COLUMN release_kind VARCHAR"),
    ("repos", "release_name", "ALTER TABLE repos ADD COLUMN release_name VARCHAR"),
    ("repos", "release_url", "ALTER TABLE repos ADD COLUMN release_url VARCHAR"),
    ("repos", "release_at", "ALTER TABLE repos ADD COLUMN release_at DATETIME"),
    ("repos", "open_pr_count", "ALTER TABLE repos ADD COLUMN open_pr_count INTEGER"),
    ("repos", "is_private", "ALTER TABLE repos ADD COLUMN is_private BOOLEAN DEFAULT 0"),
)


def _apply_ad_hoc_migrations(engine: Engine) -> None:
    """Add missing columns to existing DBs. Idempotent."""
    inspector = inspect(engine)
    with engine.begin() as conn:
        for table, column, statement in _AD_HOC_COLUMNS:
            if not inspector.has_table(table):
                continue
            existing = {c["name"] for c in inspector.get_columns(table)}
            if column in existing:
                continue
            conn.execute(text(statement))


@contextmanager
def session_scope(engine: Engine) -> Iterator[Session]:
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
