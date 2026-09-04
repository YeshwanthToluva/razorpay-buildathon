"""Database connection and schema management."""

from __future__ import annotations

from sqlalchemy import Engine, create_engine, text

from afin.config import Settings
from afin.db.schema import ADDITIVE_COLUMNS_DDL, APPEND_ONLY_DDL, metadata


def get_engine(url: str | None = None) -> Engine:
    return create_engine(url or Settings.load().database_url, future=True)


def create_schema(engine: Engine, *, drop: bool = False) -> None:
    if drop:
        with engine.begin() as conn:
            # Triggers block DELETE on audit_events, but not DROP TABLE.
            conn.execute(text("DROP TABLE IF EXISTS audit_events CASCADE"))
        metadata.drop_all(engine)
    metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text(ADDITIVE_COLUMNS_DDL))
        conn.execute(text(APPEND_ONLY_DDL))
