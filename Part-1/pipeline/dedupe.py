"""Semantic near-duplicate detection over embedded snippets.

For each embedded snippet, finds neighbours with cosine similarity ≥ threshold
(default 0.95) via Weaviate near-vector query. If any neighbour has an older
`ingested_at`, this snippet is marked as `dup_of` that earlier snippet in
`snippet_quality`.

Reuses embeddings — no new OpenAI calls. Cost ≈ 0.

Usage:
    python -m pipeline.dedupe --threshold 0.95
"""
from __future__ import annotations

import argparse
import logging
from typing import Any

from sqlalchemy import text
from tqdm import tqdm
from weaviate.classes.query import MetadataQuery

from .embed import CLASS_NAME, wclient
from .storage import engine

log = logging.getLogger(__name__)

_ENSURE_TABLE = text(
    """
    CREATE TABLE IF NOT EXISTS snippet_quality (
        snippet_id       TEXT PRIMARY KEY REFERENCES raw_snippets(id) ON DELETE CASCADE,
        lang             TEXT,
        is_spam          BOOLEAN,
        spam_kind        TEXT,
        is_relevant      BOOLEAN,
        dup_of           TEXT REFERENCES raw_snippets(id) ON DELETE SET NULL,
        behaviour_flags  JSONB,
        specificity      INT,
        clarity          INT,
        actionability    INT,
        novelty          NUMERIC(4,3),
        info_value_score NUMERIC(5,2),
        weight_recency   NUMERIC(5,3),
        weight_region    TEXT,
        updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    CREATE INDEX IF NOT EXISTS snippet_quality_dup_idx ON snippet_quality(dup_of);
    CREATE INDEX IF NOT EXISTS snippet_quality_info_idx ON snippet_quality(info_value_score DESC);
    """
)


def _ensure_row(conn, snippet_id: str) -> None:
    conn.execute(
        text("INSERT INTO snippet_quality (snippet_id) VALUES (:sid) ON CONFLICT (snippet_id) DO NOTHING"),
        {"sid": snippet_id},
    )


def _pending_batch(limit: int) -> list[dict]:
    """Snippets embedded but not yet checked for duplicates.

    Uses a helper column `snippet_quality._dedup_checked_at` to track completion.
    Ensures we don't rescan already-checked snippets."""
    with engine().begin() as conn:
        conn.execute(text(
            "ALTER TABLE snippet_quality ADD COLUMN IF NOT EXISTS _dedup_checked_at TIMESTAMPTZ"
        ))
    q = text(
        """
        SELECT es.snippet_id, r.ingested_at
        FROM embedded_snippets es
        JOIN raw_snippets r ON r.id = es.snippet_id
        LEFT JOIN snippet_quality sq ON sq.snippet_id = es.snippet_id
        WHERE sq._dedup_checked_at IS NULL
        ORDER BY r.ingested_at
        LIMIT :lim
        """
    )
    with engine().begin() as conn:
        return [dict(r._mapping) for r in conn.execute(q, {"lim": limit})]


def _fetch_vector(coll, snippet_id: str) -> list[float] | None:
    from weaviate.classes.query import Filter
    res = coll.query.fetch_objects(
        filters=Filter.by_property("snippet_id").equal(snippet_id),
        limit=1,
        include_vector=True,
    )
    for o in res.objects:
        v = o.vector.get("default") if isinstance(o.vector, dict) else o.vector
        if v:
            return list(v)
    return None


def run(threshold: float = 0.95, batch_size: int = 500, max_neighbours: int = 5) -> int:
    with engine().begin() as conn:
        conn.execute(_ENSURE_TABLE)

    coll = wclient().collections.get(CLASS_NAME)
    total_scanned = 0
    total_dupes = 0

    while True:
        rows = _pending_batch(batch_size)
        if not rows:
            break
        pairs: list[dict] = []
        for r in tqdm(rows, desc="dedup", leave=False):
            sid = r["snippet_id"]
            vec = _fetch_vector(coll, sid)
            if vec is None:
                continue
            # Find near-neighbours by vector
            near = coll.query.near_vector(
                near_vector=vec,
                limit=max_neighbours + 1,   # +1 because self will be in results
                return_metadata=MetadataQuery(distance=True),
            )
            dup_of = None
            for o in near.objects:
                other_id = o.properties.get("snippet_id")
                if not other_id or other_id == sid:
                    continue
                # Weaviate cosine: distance = 1 - cosine_similarity
                dist = o.metadata.distance if o.metadata else None
                if dist is None:
                    continue
                sim = 1.0 - dist
                if sim < threshold:
                    break  # results are sorted; we can stop
                # Take earliest ingested_at as canonical
                earlier = _earlier_ingested(other_id, r["ingested_at"])
                if earlier == other_id:
                    dup_of = other_id
                    break
            pairs.append({"sid": sid, "dup_of": dup_of})
        # Bulk upsert — always mark dedup_checked, only set dup_of when found
        if pairs:
            with engine().begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO snippet_quality (snippet_id, dup_of, _dedup_checked_at, updated_at) "
                        "VALUES (:sid, :dup_of, NOW(), NOW()) "
                        "ON CONFLICT (snippet_id) DO UPDATE "
                        "SET dup_of=EXCLUDED.dup_of, _dedup_checked_at=NOW(), updated_at=NOW()"
                    ),
                    pairs,
                )
        total_scanned += len(rows)
        total_dupes += sum(1 for p in pairs if p["dup_of"])
        log.info("dedup: scanned=%d dupes=%d (batch %d)", total_scanned, total_dupes, len(rows))
        if len(rows) < batch_size:
            break

    log.info("dedup: DONE. scanned=%d dupes=%d (rate %.1f%%)",
             total_scanned, total_dupes, 100.0 * total_dupes / (total_scanned or 1))
    return total_dupes


def _earlier_ingested(other_id: str, my_ts) -> str | None:
    """Return the id of whichever snippet has the earlier ingested_at."""
    with engine().begin() as conn:
        other_ts = conn.execute(
            text("SELECT ingested_at FROM raw_snippets WHERE id = :id"),
            {"id": other_id},
        ).scalar_one_or_none()
    if other_ts is None:
        return None
    return other_id if other_ts <= my_ts else None


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--threshold", type=float, default=0.95)
    ap.add_argument("--batch-size", type=int, default=500)
    ap.add_argument("--max-neighbours", type=int, default=5)
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args()
    logging.basicConfig(level=args.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    run(args.threshold, args.batch_size, args.max_neighbours)
