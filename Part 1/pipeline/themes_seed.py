"""Phase C step 1 — Seed the taxonomy with 10-15 parent themes.

Selects 200 random high-info-value snippets (info_value_score ≥ SEED_MIN_SCORE)
from `snippet_quality`, sends them to GPT-4.1 with the seed prompt, and writes
the returned themes into `themes` (status='seed', taxonomy_version=1).

For each seeded theme, the canonical example's embedding becomes the initial
centroid. As reviews are assigned to the theme in Phase C.2, centroids get
updated to the running mean.

Usage:
    python -m pipeline.themes_seed --n 200 --model gpt-4.1
"""
from __future__ import annotations

import argparse
import json
import logging
from typing import Any

import numpy as np
from sqlalchemy import text

from .embed import CLASS_NAME, wclient
from .openai_client import SYNTHESIZE_MODEL, chat_json
from .settings import load_prompt
from .storage import engine

log = logging.getLogger(__name__)

PROMPT_VERSION = "themes_seed_v1"
_PROMPT = load_prompt("themes_seed_v1.txt")

SEED_MIN_SCORE = 50.0  # keep the top ~half of scored snippets
TAXONOMY_VERSION = 1


def _fetch_embedding(coll, snippet_id: str) -> list[float] | None:
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


def _sample(n: int, min_score: float) -> list[dict[str, Any]]:
    q = text(
        """
        SELECT r.id AS snippet_id, r.text, r.source, r.brand, sq.info_value_score
        FROM raw_snippets r
        JOIN snippet_quality sq ON sq.snippet_id = r.id
        JOIN embedded_snippets es ON es.snippet_id = r.id
        WHERE sq.is_spam = false
          AND sq.is_relevant = true
          AND sq.dup_of IS NULL
          AND sq.info_value_score >= :min_score
          AND length(r.text) BETWEEN 60 AND 1200
        ORDER BY random()
        LIMIT :n
        """
    )
    with engine().begin() as conn:
        return [dict(r._mapping) for r in conn.execute(q, {"min_score": min_score, "n": n}).all()]


def _format_reviews(sample: list[dict]) -> str:
    lines = []
    for r in sample:
        txt = (r["text"] or "").replace("\n", " ").strip()[:500]
        lines.append(f"[id={r['snippet_id']}][{r['source']}/{r['brand']}] {txt}")
    return "\n".join(lines)


def _sanitize_themes(raw: dict) -> list[dict]:
    themes = raw.get("themes") or []
    out = []
    for t in themes:
        name = (t.get("name") or "").strip()
        defn = (t.get("definition") or "").strip()
        ex_id = (t.get("canonical_example_id") or "").strip()
        if name and defn and ex_id:
            out.append({
                "name": name[:120],
                "definition": defn[:500],
                "canonical_example_id": ex_id,
                "canonical_example_quote": (t.get("canonical_example_quote") or "")[:400],
            })
    return out


def run(n: int = 200, min_score: float = SEED_MIN_SCORE, model: str = SYNTHESIZE_MODEL) -> int:
    # 1. Ensure taxonomy version exists
    with engine().begin() as conn:
        conn.execute(text(
            "INSERT INTO taxonomy_versions (version) VALUES (:v) ON CONFLICT DO NOTHING"
        ), {"v": TAXONOMY_VERSION})

    # 2. Sample high-info snippets
    sample = _sample(n, min_score)
    if len(sample) < 20:
        log.warning("themes_seed: only %d snippets qualify (min_score=%.1f). Aborting.",
                    len(sample), min_score)
        return 0
    log.info("themes_seed: sampled %d snippets (target=%d)", len(sample), n)

    # 3. LLM propose themes
    prompt = _PROMPT.replace("{reviews_block}", _format_reviews(sample))
    log.info("themes_seed: prompt %d chars → %s", len(prompt), model)
    raw = chat_json(prompt, model=model, temperature=0.3)
    themes = _sanitize_themes(raw)
    if not themes:
        log.error("themes_seed: model returned no valid themes; raw=%s", str(raw)[:400])
        return 0
    log.info("themes_seed: model proposed %d themes", len(themes))

    # 4. Fetch embeddings for canonical examples (seed centroids)
    coll = wclient().collections.get(CLASS_NAME)
    valid_ids = {r["snippet_id"] for r in sample}
    to_insert = []
    for t in themes:
        ex_id = t["canonical_example_id"]
        if ex_id not in valid_ids:
            # Model made up an id — pick a random snippet as fallback
            log.warning("themes_seed: theme '%s' referenced unknown id %s; using random",
                        t["name"], ex_id)
            ex_id = sample[0]["snippet_id"]
            t["canonical_example_id"] = ex_id
        vec = _fetch_embedding(coll, ex_id)
        if vec is None:
            log.warning("themes_seed: no embedding for %s; skipping theme '%s'",
                        ex_id, t["name"])
            continue
        to_insert.append({
            "name": t["name"],
            "definition": t["definition"],
            "centroid": vec,
            "canonical_snippet_id": ex_id,
            "canonical_quote": t["canonical_example_quote"],
        })

    if not to_insert:
        log.error("themes_seed: no themes with valid embeddings")
        return 0

    # 5. Insert into themes table + auto-assign the canonical snippet to its theme
    with engine().begin() as conn:
        for t in to_insert:
            theme_id = conn.execute(text(
                "INSERT INTO themes (name, definition, embedding_centroid, taxonomy_version, status) "
                "VALUES (:name, :defn, :centroid, :v, 'seed') RETURNING id"
            ), {
                "name": t["name"],
                "defn": t["definition"],
                "centroid": t["centroid"],
                "v": TAXONOMY_VERSION,
            }).scalar_one()
            conn.execute(text(
                "INSERT INTO review_themes (snippet_id, theme_id, similarity, assigned_by, taxonomy_version) "
                "VALUES (:sid, :tid, 1.0, 'seed', :v) ON CONFLICT DO NOTHING"
            ), {"sid": t["canonical_snippet_id"], "tid": theme_id, "v": TAXONOMY_VERSION})

    log.info("themes_seed: inserted %d themes (taxonomy v%d)", len(to_insert), TAXONOMY_VERSION)
    return len(to_insert)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--min-score", type=float, default=SEED_MIN_SCORE)
    ap.add_argument("--model", default=SYNTHESIZE_MODEL)
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args()
    logging.basicConfig(level=args.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    run(args.n, args.min_score, args.model)
