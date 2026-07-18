"""Shared storage: raw_snippets writer (Parquet + Postgres upsert)."""
from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from .settings import DATABASE_URL, DATA_LAKE_DIR

log = logging.getLogger(__name__)

_WHITESPACE_RE = re.compile(r"\s+")


def normalize_text(t: str) -> str:
    return _WHITESPACE_RE.sub(" ", (t or "").strip().lower())


def snippet_id(source: str, text_val: str) -> str:
    h = hashlib.sha256(f"{source}|{normalize_text(text_val)}".encode("utf-8")).hexdigest()
    return h[:32]


@dataclass
class Snippet:
    source: str
    text: str
    brand: str = "zepto"
    source_url: str | None = None
    author: str | None = None
    posted_at: datetime | None = None
    lang: str | None = None
    rating: int | None = None
    raw_metadata: dict[str, Any] = field(default_factory=dict)

    def to_row(self) -> dict[str, Any]:
        norm = normalize_text(self.text)
        sid = snippet_id(f"{self.source}:{self.brand}", self.text)
        return {
            "id": sid,
            "source": self.source,
            "brand": self.brand,
            "source_url": self.source_url,
            "text": self.text,
            "text_normalized": norm,
            "author": self.author,
            "posted_at": self.posted_at,
            "lang": self.lang,
            "rating": self.rating,
            "raw_metadata": self.raw_metadata,
        }


_engine: Engine | None = None


def engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = create_engine(DATABASE_URL, pool_pre_ping=True, future=True)
    return _engine


def write_parquet(source: str, rows: list[dict[str, Any]]) -> Path:
    if not rows:
        return Path("/dev/null")
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    outdir = DATA_LAKE_DIR / source
    outdir.mkdir(parents=True, exist_ok=True)
    path = outdir / f"{day}_{datetime.now(timezone.utc).strftime('%H%M%S')}.parquet"
    df = pd.DataFrame(rows)
    # JSONB / dict columns → JSON strings for Parquet
    if "raw_metadata" in df.columns:
        df["raw_metadata"] = df["raw_metadata"].apply(lambda v: json.dumps(v, default=str) if isinstance(v, (dict, list)) else v)
    df.to_parquet(path, index=False)
    log.info("wrote %d rows → %s", len(rows), path)
    return path


UPSERT_SQL = text(
    """
    INSERT INTO raw_snippets (id, source, brand, source_url, text, text_normalized,
                              author, posted_at, lang, rating, raw_metadata)
    VALUES (:id, :source, :brand, :source_url, :text, :text_normalized,
            :author, :posted_at, :lang, :rating, CAST(:raw_metadata AS JSONB))
    ON CONFLICT (id) DO NOTHING
    """
)


def upsert_snippets(snippets: Iterable[Snippet]) -> int:
    rows = []
    for s in snippets:
        r = s.to_row()
        if not r["text"] or len(r["text"].strip()) < 10:
            continue
        r["raw_metadata"] = json.dumps(r["raw_metadata"], default=str)
        rows.append(r)
    if not rows:
        return 0
    with engine().begin() as conn:
        conn.execute(UPSERT_SQL, rows)
    log.info("upserted %d snippets into raw_snippets", len(rows))
    # also parquet-archive (with the *dict* metadata for readability)
    parquet_rows = [{**r, "raw_metadata": json.loads(r["raw_metadata"])} for r in rows]
    if parquet_rows:
        source = parquet_rows[0]["source"]
        write_parquet(source, parquet_rows)
    return len(rows)
