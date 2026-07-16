"""Phase C step 4 — Promote candidate pool clusters into new themes.

Runs after themes_assign. Takes all snippets in `theme_candidates` (those
that didn't match any existing theme with sim ≥ 0.75), clusters them with
DBSCAN, and for each cluster with:
  - ≥ MIN_MEMBERS members
  - mean intra-cluster cosine ≥ MIN_INTRA_SIM
  → asks GPT-4.1 to name the cluster, then inserts it as a new theme in
    the current taxonomy version. Cluster members move from
    `theme_candidates` to `review_themes`.

Usage:
    python -m pipeline.themes_promote --min-members 8 --eps 0.30
"""
from __future__ import annotations

import argparse
import json
import logging

import numpy as np
from sklearn.cluster import DBSCAN
from sklearn.metrics.pairwise import cosine_similarity
from sqlalchemy import text

from .embed import CLASS_NAME, wclient
from .openai_client import SYNTHESIZE_MODEL, chat_json
from .settings import load_prompt
from .storage import engine

log = logging.getLogger(__name__)

TAXONOMY_VERSION = 1
MIN_MEMBERS = 8
MIN_INTRA_SIM = 0.72

_NAME_PROMPT = """You are helping expand a taxonomy of user-behavior themes for Indian
quick-commerce apps. Below are ~15 real reviews that CLUSTER together
semantically but don't fit any existing theme. Propose ONE stable, punchy
parent theme (2-6 words) + a one-sentence definition + which review is the
best canonical example.

REVIEWS:
{block}

EXISTING THEME NAMES (do not duplicate these):
{existing_names}

Return only json:
{
  "name": "<2-6 word theme name>",
  "definition": "<one sentence, <=180 chars>",
  "canonical_example_id": "<id from the input>"
}
"""


def _fetch_candidates_with_vectors() -> list[dict]:
    from weaviate.classes.query import Filter
    q = text(
        """
        SELECT tc.snippet_id, r.text
        FROM theme_candidates tc
        JOIN raw_snippets r ON r.id = tc.snippet_id
        ORDER BY tc.entered_pool_at
        """
    )
    with engine().begin() as conn:
        rows = list(conn.execute(q).all())
    if not rows:
        return []
    coll = wclient().collections.get(CLASS_NAME)
    sids = [r.snippet_id for r in rows]
    vec_map: dict[str, np.ndarray] = {}
    CHUNK = 100
    for i in range(0, len(sids), CHUNK):
        chunk = sids[i : i + CHUNK]
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
                    n = np.linalg.norm(vec)
                    if n > 0:
                        vec_map[sid] = vec / n
    out = []
    for r in rows:
        if r.snippet_id in vec_map:
            out.append({"snippet_id": r.snippet_id, "text": r.text, "vec": vec_map[r.snippet_id]})
    return out


def _cluster(vectors: np.ndarray, eps: float, min_samples: int) -> np.ndarray:
    return DBSCAN(eps=eps, min_samples=min_samples, metric="cosine").fit_predict(vectors)


def _existing_theme_names() -> list[str]:
    with engine().begin() as conn:
        return [r[0] for r in conn.execute(text(
            "SELECT name FROM themes WHERE status IN ('seed', 'promoted')"
        )).all()]


def _name_cluster(members: list[dict], existing_names: list[str]) -> dict | None:
    sample_lines = []
    for m in members[:15]:
        t = (m["text"] or "").replace("\n", " ").strip()[:400]
        sample_lines.append(f"[id={m['snippet_id']}] {t}")
    prompt = (_NAME_PROMPT
              .replace("{block}", "\n".join(sample_lines))
              .replace("{existing_names}", "\n".join(f"- {n}" for n in existing_names)))
    try:
        raw = chat_json(prompt, model=SYNTHESIZE_MODEL, temperature=0.3)
    except Exception as e:
        log.warning("promote: name call failed: %s", str(e)[:200])
        return None
    name = (raw.get("name") or "").strip()
    defn = (raw.get("definition") or "").strip()
    if not name or not defn:
        return None
    return {
        "name": name[:120],
        "definition": defn[:500],
        "canonical_example_id": raw.get("canonical_example_id") or members[0]["snippet_id"],
    }


def run(min_members: int = MIN_MEMBERS, min_intra_sim: float = MIN_INTRA_SIM,
        eps: float = 0.30) -> int:
    candidates = _fetch_candidates_with_vectors()
    if len(candidates) < min_members:
        log.info("promote: only %d candidates, need ≥ %d. Skipping.",
                 len(candidates), min_members)
        return 0
    log.info("promote: clustering %d candidates (eps=%.2f, min_samples=%d)",
             len(candidates), eps, min_members)
    X = np.stack([c["vec"] for c in candidates])
    labels = _cluster(X, eps=eps, min_samples=min_members)
    unique = sorted(set(labels) - {-1})
    log.info("promote: DBSCAN found %d clusters (noise=%d)",
             len(unique), int((labels == -1).sum()))
    existing = _existing_theme_names()

    promoted = 0
    with engine().begin() as conn:
        for lbl in unique:
            member_idx = [i for i, l in enumerate(labels) if l == lbl]
            members = [candidates[i] for i in member_idx]
            sub = X[member_idx]
            sim = cosine_similarity(sub)
            iu = np.triu_indices_from(sim, k=1)
            intra = float(sim[iu].mean()) if len(iu[0]) else 1.0
            if intra < min_intra_sim:
                log.info("promote: cluster %d intra=%.3f < %.2f — skipping",
                         lbl, intra, min_intra_sim)
                continue

            named = _name_cluster(members, existing)
            if not named:
                continue

            # centroid = mean vector
            centroid = sub.mean(axis=0)
            n = np.linalg.norm(centroid)
            if n > 0:
                centroid = centroid / n

            theme_id = conn.execute(text(
                "INSERT INTO themes (name, definition, embedding_centroid, taxonomy_version, status) "
                "VALUES (:name, :defn, :centroid, :v, 'promoted') RETURNING id"
            ), {
                "name": named["name"],
                "defn": named["definition"],
                "centroid": centroid.tolist(),
                "v": TAXONOMY_VERSION,
            }).scalar_one()

            # Move all members from candidates to review_themes
            for m in members:
                sim_to_centroid = float(centroid @ m["vec"])
                conn.execute(text(
                    "INSERT INTO review_themes (snippet_id, theme_id, similarity, assigned_by, taxonomy_version) "
                    "VALUES (:sid, :tid, :sim, 'promotion', :v) "
                    "ON CONFLICT DO NOTHING"
                ), {"sid": m["snippet_id"], "tid": theme_id, "sim": round(sim_to_centroid, 3),
                    "v": TAXONOMY_VERSION})
                conn.execute(text("DELETE FROM theme_candidates WHERE snippet_id = :sid"),
                             {"sid": m["snippet_id"]})

            existing.append(named["name"])
            promoted += 1
            log.info("promote: new theme #%d '%s' (intra=%.3f, members=%d)",
                     theme_id, named["name"], intra, len(members))

    log.info("promote: DONE promoted=%d themes", promoted)
    return promoted


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-members", type=int, default=MIN_MEMBERS)
    ap.add_argument("--min-intra-sim", type=float, default=MIN_INTRA_SIM)
    ap.add_argument("--eps", type=float, default=0.30)
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args()
    logging.basicConfig(level=args.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    run(args.min_members, args.min_intra_sim, args.eps)
