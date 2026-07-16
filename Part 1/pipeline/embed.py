"""Embed filtered snippets with OpenAI text-embedding-3-small and store in Weaviate.

We treat Weaviate as a pure vector index; ground truth stays in Postgres. Each
Weaviate object stores (snippet_id, source) — everything else joined in later.
"""
from __future__ import annotations

import logging
from typing import Iterable

import weaviate
from weaviate.classes.config import Configure, DataType, Property
from weaviate.classes.query import Filter
from sqlalchemy import text

from .openai_client import embed_batch
from .settings import WEAVIATE_URL
from .storage import engine

log = logging.getLogger(__name__)

CLASS_NAME = "Snippet"
_client: weaviate.WeaviateClient | None = None


def wclient() -> weaviate.WeaviateClient:
    global _client
    if _client is None:
        # weaviate v4 client — connect to local
        host = WEAVIATE_URL.replace("http://", "").replace("https://", "")
        host, _, port = host.partition(":")
        _client = weaviate.connect_to_local(host=host or "localhost", port=int(port or 8080))
    return _client


def ensure_schema() -> None:
    c = wclient()
    if c.collections.exists(CLASS_NAME):
        return
    c.collections.create(
        name=CLASS_NAME,
        properties=[
            Property(name="snippet_id", data_type=DataType.TEXT),
            Property(name="source", data_type=DataType.TEXT),
            Property(name="text", data_type=DataType.TEXT),
        ],
        vectorizer_config=Configure.Vectorizer.none(),
    )
    log.info("weaviate: created collection %s", CLASS_NAME)


_ENSURE_EMBEDDED_TABLE = text(
    """
    CREATE TABLE IF NOT EXISTS embedded_snippets (
        snippet_id  TEXT PRIMARY KEY REFERENCES raw_snippets(id) ON DELETE CASCADE,
        embedded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    """
)


def _pending_batch(limit: int) -> list[dict]:
    q = text(
        """
        SELECT f.snippet_id, r.source, r.text
        FROM filtered_snippets f
        JOIN raw_snippets r ON r.id = f.snippet_id
        LEFT JOIN embedded_snippets es ON es.snippet_id = f.snippet_id
        WHERE es.snippet_id IS NULL
        ORDER BY f.filtered_at
        LIMIT :lim
        """
    )
    with engine().begin() as conn:
        conn.execute(_ENSURE_EMBEDDED_TABLE)
        return [dict(r._mapping) for r in conn.execute(q, {"lim": limit})]


def _mark_embedded(snippet_ids: list[str]) -> None:
    if not snippet_ids:
        return
    with engine().begin() as conn:
        conn.execute(
            text("INSERT INTO embedded_snippets (snippet_id) VALUES (:sid) ON CONFLICT DO NOTHING"),
            [{"sid": sid} for sid in snippet_ids],
        )


def embed_and_index(batch_size: int = 100, max_batches: int = 100) -> int:
    ensure_schema()
    coll = wclient().collections.get(CLASS_NAME)
    total = 0
    for _ in range(max_batches):
        rows = _pending_batch(batch_size)
        if not rows:
            break
        texts = [r["text"][:8000] for r in rows]
        vectors = embed_batch(texts)
        with coll.batch.dynamic() as batch:
            for r, v in zip(rows, vectors):
                batch.add_object(
                    properties={"snippet_id": r["snippet_id"], "source": r["source"], "text": r["text"][:8000]},
                    vector=v,
                )
        _mark_embedded([r["snippet_id"] for r in rows])
        total += len(rows)
        log.info("embed: indexed %d (running total %d)", len(rows), total)
    return total


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch-size", type=int, default=100)
    ap.add_argument("--max-batches", type=int, default=100)
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args()
    logging.basicConfig(level=args.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    embed_and_index(args.batch_size, args.max_batches)
