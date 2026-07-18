"""Data-access layer for the dashboard.

Tries Postgres first (local dev). On any connection error, falls back to
DuckDB reading the Parquet snapshots in `Part 1/demo_data/`. This is what
makes the same dashboard code work both locally and on Streamlit Community
Cloud (which can't reach a local Postgres).

Usage in dashboard:
    from dashboard.data import query, is_demo_mode

    df = query("SELECT * FROM insight_cards ORDER BY confidence DESC")
"""
from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
DEMO_DIR = ROOT / "demo_data"


@lru_cache(maxsize=1)
def _pg_engine():
    if os.environ.get("FORCE_DEMO_MODE") == "1":
        return None
    try:
        from pipeline.storage import engine
        eng = engine()
        # cheap ping
        with eng.connect() as c:
            c.exec_driver_sql("SELECT 1")
        return eng
    except (ImportError, ModuleNotFoundError) as e:
        log.info("pipeline import failed, DEMO mode: %s", str(e)[:120])
        return None
    except Exception as e:
        log.info("Postgres unavailable, DEMO mode: %s", str(e)[:120])
        return None


@lru_cache(maxsize=1)
def _duck():
    import duckdb
    con = duckdb.connect(":memory:")
    if not DEMO_DIR.exists():
        log.warning("demo_data/ missing — dashboard will show empty results")
        return con
    for p in sorted(DEMO_DIR.glob("*.parquet")):
        table_name = p.stem
        con.execute(f"CREATE VIEW {table_name} AS SELECT * FROM read_parquet('{p.as_posix()}')")
        log.debug("duckdb: registered view %s ← %s", table_name, p.name)
    return con


def is_demo_mode() -> bool:
    """True when we can't reach Postgres (Streamlit Cloud, or Docker off)."""
    if os.environ.get("FORCE_DEMO_MODE") == "1":
        return True
    return _pg_engine() is None


def query(sql: str, params: dict | None = None) -> pd.DataFrame:
    """Run a SQL query, returning a DataFrame. Uses Postgres if reachable, else DuckDB+Parquet.

    NOTE: the dashboard queries are tuned to be portable — no Postgres-only
    functions in the WHERE/SELECT except unnest() and JSON operators, which
    DuckDB supports too."""
    if is_demo_mode():
        # DuckDB uses ? placeholders; convert :named to positional if needed
        con = _duck()
        if params:
            # Convert :name → ? placeholders
            import re as _re
            keys = _re.findall(r":(\w+)", sql)
            duck_sql = _re.sub(r":\w+", "?", sql)
            values = [params[k] for k in keys]
            return con.execute(duck_sql, values).df()
        return con.execute(sql).df()
    else:
        from sqlalchemy import text
        with _pg_engine().begin() as conn:
            return pd.read_sql(text(sql), conn, params=params or {})


def has_table(table: str) -> bool:
    if is_demo_mode():
        con = _duck()
        try:
            r = con.execute(
                "SELECT 1 FROM information_schema.tables WHERE table_name = ?", [table]
            ).fetchone()
            return r is not None
        except Exception:
            return False
    else:
        from sqlalchemy import text
        with _pg_engine().begin() as conn:
            return bool(conn.execute(
                text("SELECT 1 FROM information_schema.tables WHERE table_name=:n"),
                {"n": table},
            ).first())
