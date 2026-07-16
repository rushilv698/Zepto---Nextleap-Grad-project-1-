"""Phase C step 2 — Assign snippets to nearest theme by cosine similarity.

For each non-spam, non-dup, relevant snippet not yet in `review_themes`,
computes cosine similarity against all theme centroids. Assigns to nearest if
similarity ≥ THRESHOLD, else defers to `theme_candidates`.

Also updates each theme's centroid to the running mean of its assigned
snippets' embeddings (batched update every 500 assignments).

Usage:
    python -m pipeline.themes_assign --threshold 0.75
"""
from __future__ import annotations

import argparse
import logging

import numpy as np
from sqlalchemy import text
from tqdm import tqdm

from .embed import CLASS_NAME, wclient
from .storage import engine

log = logging.getLogger(__name__)

TAXONOMY_VERSION = 1
DEFAULT_THRESHOLD = 0.75


def _load_theme_centroids() -> list[dict]:
    q = text("SELECT id, name, embedding_centroid FROM themes WHERE status IN ('seed', 'promoted')")
    with engine().begin() as conn:
        rows = list(conn.execute(q).all())
    themes = []
    for r in rows:
        centroid = r.embedding_centroid
        if centroid is None:
            continue
        vec = np.array(centroid, dtype=np.float32)
        norm = np.linalg.norm(vec)
        if norm == 0:
            continue
        themes.append({"id": r.id, "name": r.name, "vec": vec / norm})
    return themes


def _fetch_pending(limit: int) -> list[dict]:
    q = text(
        """
        SELECT r.id AS snippet_id
        FROM raw_snippets r
        JOIN snippet_quality sq ON sq.snippet_id = r.id
        JOIN embedded_snippets es ON es.snippet_id = r.id
        LEFT JOIN review_themes rt ON rt.snippet_id = r.id AND rt.taxonomy_version = :v
        LEFT JOIN theme_candidates tc ON tc.snippet_id = r.id
        WHERE sq.is_spam = false
          AND sq.is_relevant = true
          AND sq.dup_of IS NULL
          AND rt.snippet_id IS NULL
          AND tc.snippet_id IS NULL
        ORDER BY sq.info_value_score DESC NULLS LAST
        LIMIT :lim
        """
    )
    with engine().begin() as conn:
        return [dict(r._mapping) for r in conn.execute(q, {"v": TAXONOMY_VERSION, "lim": limit}).all()]


def _fetch_vectors_batch(coll, snippet_ids: list[str]) -> dict[str, np.ndarray]:
    from weaviate.classes.query import Filter
    out: dict[str, np.ndarray] = {}
    CHUNK = 100
    for i in range(0, len(snippet_ids), CHUNK):
        chunk = snippet_ids[i : i + CHUNK]
        res = coll.query.fetch_objects(
            filters=Filter.by_property("snippet_id").contains_any(chunk),
            limit=len(chunk),
            include_vector=True,
        )
        for o in res.objects:
            sid = o.properties.get("snippet_id")
            if sid and o.vector:
                v = o.vector.get("default") if isinstance(o.vector, dict) else o.vector
                if v:
                    vec = np.array(v, dtype=np.float32)
                    norm = np.linalg.norm(vec)
                    if norm > 0:
                        out[sid] = vec / norm
    return out


def _batch_assign(rows: list[dict], themes: list[dict], threshold: float, coll) -> tuple[int, int]:
    vec_map = _fetch_vectors_batch(coll, [r["snippet_id"] for r in rows])
    theme_matrix = np.stack([t["vec"] for t in themes])  # (n_themes, dim)
    assigns, defers = [], []
    for r in rows:
        vec = vec_map.get(r["snippet_id"])
        if vec is None:
            defers.append({"sid": r["snippet_id"], "score": 0.0})
            continue
        sims = theme_matrix @ vec  # cosine because both are unit-norm
        best_idx = int(np.argmax(sims))
        best_sim = float(sims[best_idx])
        if best_sim >= threshold:
            assigns.append({
                "sid": r["snippet_id"],
                "tid": themes[best_idx]["id"],
                "sim": round(best_sim, 3),
            })
        else:
            defers.append({"sid": r["snippet_id"], "score": round(best_sim, 3)})
    with engine().begin() as conn:
        if assigns:
            conn.execute(text(
                "INSERT INTO review_themes (snippet_id, theme_id, similarity, assigned_by, taxonomy_version) "
                "VALUES (:sid, :tid, :sim, 'cosine', :v) "
                "ON CONFLICT (snippet_id, theme_id) DO NOTHING"
            ), [{**a, "v": TAXONOMY_VERSION} for a in assigns])
        if defers:
            conn.execute(text(
                "INSERT INTO theme_candidates (snippet_id, info_value_score) "
                "VALUES (:sid, :score) ON CONFLICT DO NOTHING"
            ), defers)
    return len(assigns), len(defers)


def _update_centroids() -> None:
    """Recompute each theme centroid as the mean of its assigned embeddings.

    This tightens the taxonomy over time as more evidence accumulates.
    """
    q_theme_snippets = text(
        "SELECT theme_id, array_agg(snippet_id) AS snippet_ids "
        "FROM review_themes WHERE taxonomy_version = :v GROUP BY theme_id"
    )
    coll = wclient().collections.get(CLASS_NAME)
    with engine().begin() as conn:
        rows = list(conn.execute(q_theme_snippets, {"v": TAXONOMY_VERSION}).all())
    for r in rows:
        vec_map = _fetch_vectors_batch(coll, r.snippet_ids)
        if not vec_map:
            continue
        stack = np.stack(list(vec_map.values()))
        mean = stack.mean(axis=0)
        n = np.linalg.norm(mean)
        if n > 0:
            mean = mean / n
        with engine().begin() as conn:
            conn.execute(text(
                "UPDATE themes SET embedding_centroid = :c WHERE id = :id"
            ), {"c": mean.tolist(), "id": r.theme_id})
    log.info("themes_assign: updated %d theme centroids", len(rows))


def run(threshold: float = DEFAULT_THRESHOLD, batch: int = 500,
        max_batches: int = 40, refresh_centroids: bool = True) -> tuple[int, int]:
    themes = _load_theme_centroids()
    if not themes:
        log.error("themes_assign: no themes loaded. Run themes_seed first.")
        return 0, 0
    coll = wclient().collections.get(CLASS_NAME)
    total_assigns, total_defers = 0, 0
    log.info("themes_assign: %d themes loaded, threshold=%.2f", len(themes), threshold)
    for i in range(max_batches):
        rows = _fetch_pending(batch)
        if not rows:
            break
        a, d = _batch_assign(rows, themes, threshold, coll)
        total_assigns += a
        total_defers += d
        log.info("themes_assign: batch %d — assigned=%d, deferred=%d (running: %d/%d)",
                 i + 1, a, d, total_assigns, total_defers)
    if refresh_centroids and total_assigns > 0:
        _update_centroids()
    log.info("themes_assign: DONE assigned=%d deferred=%d", total_assigns, total_defers)
    return total_assigns, total_defers


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    ap.add_argument("--batch", type=int, default=500)
    ap.add_argument("--max-batches", type=int, default=40)
    ap.add_argument("--no-refresh-centroids", action="store_true")
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args()
    logging.basicConfig(level=args.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    run(args.threshold, args.batch, args.max_batches, not args.no_refresh_centroids)
