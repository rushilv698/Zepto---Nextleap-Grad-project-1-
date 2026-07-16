"""Phase C step 6 — Periodic theme consolidation.

Every N assignments, find pairs of themes whose centroids are cosine-similar
above MERGE_HINT threshold. Ask GPT-4.1 to decide merge vs keep-separate.
On merge: pick the theme with more members as canonical, mark the other with
merged_into = canonical.id, move review_themes rows to the canonical theme,
bump taxonomy version.

Usage:
    python -m pipeline.themes_consolidate --merge-threshold 0.88
"""
from __future__ import annotations

import argparse
import logging

import numpy as np
from sqlalchemy import text

from .openai_client import SYNTHESIZE_MODEL, chat_json
from .storage import engine

log = logging.getLogger(__name__)

import os as _os
TAXONOMY_VERSION = int(_os.environ.get("V2_TAXONOMY_VERSION", "1"))
MERGE_HINT_THRESHOLD = 0.88


_MERGE_PROMPT = """You are a taxonomy curator. Two themes look semantically similar
(centroid cosine = {sim:.3f}). Decide whether they should be MERGED into one theme
or KEPT SEPARATE.

THEME A:
- Name: {a_name}
- Definition: {a_defn}
- Member count: {a_n}
- Sample quotes:
{a_quotes}

THEME B:
- Name: {b_name}
- Definition: {b_defn}
- Member count: {b_n}
- Sample quotes:
{b_quotes}

Return json only:
{
  "verdict": "<merge | keep_separate>",
  "reasoning": "<one sentence>",
  "merged_name": "<if merge: 2-6 word name for the combined theme; else empty string>",
  "merged_definition": "<if merge: one sentence; else empty string>"
}

Guidance:
- Prefer keep_separate if the themes describe DIFFERENT user mental models even if the language overlaps (e.g., 'Habitual Reorder' vs 'Reorder Because of Trust').
- Prefer merge if the themes are the same insight worded two ways.
- The bar for merging is high — false merges destroy signal.
"""


def _sample_quotes(theme_id: int, n: int = 4) -> list[str]:
    with engine().begin() as conn:
        rows = conn.execute(text(
            "SELECT r.text FROM review_themes rt "
            "JOIN raw_snippets r ON r.id = rt.snippet_id "
            "WHERE rt.theme_id = :tid AND rt.taxonomy_version = :v "
            "ORDER BY rt.similarity DESC NULLS LAST LIMIT :n"
        ), {"tid": theme_id, "v": TAXONOMY_VERSION, "n": n}).all()
    return [(r.text or "").replace("\n", " ").strip()[:180] for r in rows]


def _pair_check(a, b, model: str) -> dict | None:
    a_quotes = "\n".join(f"  - {q}" for q in _sample_quotes(a["id"]))
    b_quotes = "\n".join(f"  - {q}" for q in _sample_quotes(b["id"]))
    prompt = (_MERGE_PROMPT
              .replace("{sim}", str(a["sim_to_b"]))
              .replace("{a_name}", a["name"])
              .replace("{a_defn}", a["definition"] or "")
              .replace("{a_n}", str(a["n"]))
              .replace("{a_quotes}", a_quotes)
              .replace("{b_name}", b["name"])
              .replace("{b_defn}", b["definition"] or "")
              .replace("{b_n}", str(b["n"]))
              .replace("{b_quotes}", b_quotes))
    try:
        raw = chat_json(prompt, model=model, temperature=0.2)
    except Exception as e:
        log.warning("consolidate: pair check failed: %s", str(e)[:200])
        return None
    v = raw.get("verdict")
    if v not in {"merge", "keep_separate"}:
        return None
    return raw


def _merge(a_id: int, b_id: int, merged_name: str, merged_defn: str) -> None:
    """Merge b into a (a keeps its id but gets updated name/defn).

    All review_themes rows for b get repointed to a; b is marked merged_into=a.
    """
    with engine().begin() as conn:
        # Move members
        conn.execute(text(
            "UPDATE review_themes SET theme_id = :a WHERE theme_id = :b "
            "AND taxonomy_version = :v"
        ), {"a": a_id, "b": b_id, "v": TAXONOMY_VERSION})
        # Update a
        if merged_name:
            conn.execute(text(
                "UPDATE themes SET name = :n, definition = :d WHERE id = :id"
            ), {"n": merged_name[:120], "d": (merged_defn or "")[:500], "id": a_id})
        # Mark b as merged
        conn.execute(text(
            "UPDATE themes SET status = 'merged', merged_into = :a WHERE id = :b"
        ), {"a": a_id, "b": b_id})


def run(threshold: float = MERGE_HINT_THRESHOLD, model: str = SYNTHESIZE_MODEL) -> int:
    with engine().begin() as conn:
        themes = list(conn.execute(text(
            "SELECT t.id, t.name, t.definition, t.embedding_centroid, "
            "  (SELECT COUNT(*) FROM review_themes rt WHERE rt.theme_id = t.id "
            "     AND rt.taxonomy_version = :v) AS n "
            "FROM themes t "
            "WHERE t.taxonomy_version = :v AND t.status IN ('seed', 'promoted') "
            "  AND t.merged_into IS NULL AND t.embedding_centroid IS NOT NULL "
            "  AND t.parent_id IS NOT NULL   -- only merge leaves, not parent buckets"
        ), {"v": TAXONOMY_VERSION}).all())

    if len(themes) < 2:
        log.info("consolidate: %d themes; nothing to compare", len(themes))
        return 0

    # Compute all pairwise centroid cosines
    vecs = []
    for t in themes:
        v = np.array(t.embedding_centroid, dtype=np.float32)
        n = np.linalg.norm(v)
        vecs.append(v / n if n else v)
    X = np.stack(vecs)
    sims = X @ X.T
    n = len(themes)

    pairs = []
    for i in range(n):
        for j in range(i + 1, n):
            if float(sims[i, j]) >= threshold:
                pairs.append((i, j, float(sims[i, j])))
    pairs.sort(key=lambda p: -p[2])
    log.info("consolidate: %d candidate pairs above %.2f", len(pairs), threshold)

    merged_ids: set[int] = set()
    n_merged = 0
    for i, j, sim in pairs:
        if themes[i].id in merged_ids or themes[j].id in merged_ids:
            continue
        # Larger theme wins
        if themes[i].n >= themes[j].n:
            keep_idx, drop_idx = i, j
        else:
            keep_idx, drop_idx = j, i
        keep = themes[keep_idx]
        drop = themes[drop_idx]
        a = {"id": keep.id, "name": keep.name, "definition": keep.definition,
             "n": keep.n, "sim_to_b": round(sim, 3)}
        b = {"id": drop.id, "name": drop.name, "definition": drop.definition,
             "n": drop.n, "sim_to_b": round(sim, 3)}
        result = _pair_check(a, b, model)
        if not result:
            continue
        if result["verdict"] == "merge":
            _merge(keep.id, drop.id,
                   result.get("merged_name") or keep.name,
                   result.get("merged_definition") or keep.definition or "")
            log.info("consolidate: MERGED #%d '%s' <- #%d '%s' (sim=%.3f)",
                     keep.id, keep.name, drop.id, drop.name, sim)
            merged_ids.add(drop.id)
            n_merged += 1
        else:
            log.info("consolidate: keep separate #%d '%s' vs #%d '%s' (sim=%.3f)",
                     keep.id, keep.name, drop.id, drop.name, sim)

    if n_merged > 0:
        with engine().begin() as conn:
            conn.execute(text(
                "UPDATE taxonomy_versions SET consolidated_count = consolidated_count + :n "
                "WHERE version = :v"
            ), {"n": n_merged, "v": TAXONOMY_VERSION})
    log.info("consolidate: DONE merged=%d", n_merged)
    return n_merged


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--merge-threshold", type=float, default=MERGE_HINT_THRESHOLD)
    ap.add_argument("--model", default=SYNTHESIZE_MODEL)
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args()
    logging.basicConfig(level=args.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    run(args.merge_threshold, args.model)
