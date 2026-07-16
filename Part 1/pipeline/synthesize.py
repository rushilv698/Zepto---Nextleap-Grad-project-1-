"""Cluster extracted insights and synthesize one insight card per cluster.

Steps:
  1. Pull all extracted_insights whose intent is Exploration_Blocker /
     Repeat_Purchase_Habit / Discovery_Request / Unmet_Need, together with
     their embedding from Weaviate.
  2. DBSCAN cluster on embeddings.
  3. For each cluster, call GPT-4o synthesis prompt to write an insight card.
  4. Compute confidence score for the card.
  5. Insert macro_theme + insight_card rows.
"""
from __future__ import annotations

import json
import logging
import uuid
from collections import Counter, defaultdict

import numpy as np
from sklearn.cluster import DBSCAN
from sklearn.metrics.pairwise import cosine_similarity
from sqlalchemy import text

from .confidence import score as compute_confidence
from .embed import CLASS_NAME, wclient
from .extract import PROMPT_VERSION as EXTRACT_PROMPT_VERSION
from .openai_client import SYNTHESIZE_MODEL, chat_json
from .settings import load_prompt
from .storage import engine

log = logging.getLogger(__name__)

SYNTH_PROMPT_VERSION = "synthesize_v2"
_PROMPT = load_prompt("synthesize_v2.txt")

_VALID_PERSONAS = {"busy_professional", "homemaker", "student", "new_mover", "senior", "unknown"}
_VALID_BARRIERS = {"trust_deficit", "price_sensitivity", "lack_awareness", "no_urgent_need",
                   "decision_paralysis", "bad_past_experience", "discovery_UI",
                   "information_gap", "habit_loop"}


def _sanitize_card(card: dict, member_barriers: list[str], member_personas: list[str]) -> dict:
    """Force persona + barrier into the fixed taxonomy. If LLM invented a label,
    fall back to the dominant valid value from the cluster members."""
    from collections import Counter as _Counter
    p = card.get("persona_most_affected")
    if p not in _VALID_PERSONAS:
        pool = [x for x in member_personas if x in _VALID_PERSONAS and x != "unknown"]
        p = _Counter(pool).most_common(1)[0][0] if pool else "unknown"
    b = card.get("primary_barrier")
    if b not in _VALID_BARRIERS:
        pool = [x for x in member_barriers if x in _VALID_BARRIERS]
        b = _Counter(pool).most_common(1)[0][0] if pool else "trust_deficit"
    card["persona_most_affected"] = p
    card["primary_barrier"] = b
    return card

_ACTIVE_INTENTS = ("Exploration_Blocker", "Repeat_Purchase_Habit", "Discovery_Request", "Unmet_Need")


def _load_insights() -> list[dict]:
    q = text(
        """
        SELECT e.id, e.snippet_id, e.intent, e.themes, e.user_persona,
               e.category_currently_buying, e.category_avoiding, e.barrier_summary,
               e.emotional_tone, r.source, r.author
        FROM extracted_insights e
        JOIN raw_snippets r ON r.id = e.snippet_id
        WHERE e.prompt_version = :pv AND e.intent = ANY(:intents)
              AND e.barrier_summary IS NOT NULL AND length(e.barrier_summary) > 10
        """
    )
    with engine().begin() as conn:
        return [dict(r._mapping) for r in conn.execute(q, {"pv": EXTRACT_PROMPT_VERSION, "intents": list(_ACTIVE_INTENTS)})]


def _load_vectors(snippet_ids: list[str]) -> dict[str, list[float]]:
    """Batched Weaviate fetch by snippet_id."""
    coll = wclient().collections.get(CLASS_NAME)
    out: dict[str, list[float]] = {}
    # v4 client: use fetch_objects with filter, page in chunks
    from weaviate.classes.query import Filter
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
                    out[sid] = list(v)
    return out


def _cluster(vectors: np.ndarray, eps: float = 0.30, min_samples: int = 5) -> np.ndarray:
    # cosine distance via metric="cosine" in DBSCAN
    labels = DBSCAN(eps=eps, min_samples=min_samples, metric="cosine").fit_predict(vectors)
    return labels


def _dominant(values: list[str]) -> str:
    c = Counter(v for v in values if v and v != "unknown")
    return c.most_common(1)[0][0] if c else "unknown"


def _synthesize_card(cluster_insights: list[dict]) -> dict | None:
    summaries = [i["barrier_summary"] for i in cluster_insights if i["barrier_summary"]]
    if len(summaries) < 3:
        return None
    sample = summaries[:20]
    sources = sorted({i["source"] for i in cluster_insights})
    pair = _dominant([f"{i['category_currently_buying']}→{i['category_avoiding']}" for i in cluster_insights])
    persona = _dominant([i["user_persona"] for i in cluster_insights])
    summaries_block = "\n".join(f"- {s}" for s in sample)
    prompt = (
        _PROMPT
        .replace("{n}", str(len(cluster_insights)))
        .replace("{sources_str}", ", ".join(sources))
        .replace("{dominant_persona}", persona)
        .replace("{dominant_pair}", pair)
        .replace("{summaries_block}", summaries_block)
    )
    try:
        return chat_json(prompt, model=SYNTHESIZE_MODEL, temperature=0.3)
    except Exception as e:
        log.warning("synth failed: %s", str(e)[:200])
        return None


def run(eps: float = 0.30, min_samples: int = 5) -> int:
    insights = _load_insights()
    if len(insights) < 20:
        log.warning("synthesize: only %d insights available, skipping", len(insights))
        return 0

    vec_map = _load_vectors([i["snippet_id"] for i in insights])
    insights = [i for i in insights if i["snippet_id"] in vec_map]
    if not insights:
        log.warning("synthesize: no vectors found in weaviate")
        return 0

    X = np.array([vec_map[i["snippet_id"]] for i in insights], dtype=np.float32)
    labels = _cluster(X, eps=eps, min_samples=min_samples)
    log.info("synthesize: %d insights → %d clusters (excluding noise)", len(insights), len(set(labels) - {-1}))

    run_id = uuid.uuid4().hex[:12]
    by_cluster: dict[int, list[int]] = defaultdict(list)
    for idx, lbl in enumerate(labels):
        if lbl >= 0:
            by_cluster[int(lbl)].append(idx)

    cards_written = 0
    with engine().begin() as conn:
        for cluster_id, idxs in by_cluster.items():
            members = [insights[i] for i in idxs]
            card = _synthesize_card(members)
            if not card:
                continue
            all_barriers = [t for m in members for t in (m.get("themes") or [])]
            all_personas = [m.get("user_persona") for m in members]
            card = _sanitize_card(card, all_barriers, all_personas)

            sub_vecs = X[idxs]
            sim = cosine_similarity(sub_vecs)
            iu = np.triu_indices_from(sim, k=1)
            intra = float(sim[iu].mean()) if len(iu[0]) else 1.0

            source_counts = dict(Counter(m["source"] for m in members))
            unique_authors = len({m["author"] for m in members if m["author"]})
            tones = [m["emotional_tone"] for m in members if m["emotional_tone"]]
            conf, breakdown = compute_confidence(
                source_counts=source_counts,
                unique_authors=unique_authors,
                tones=tones,
                intra_cluster_cosine_mean=intra,
            )

            mt_id = conn.execute(
                text(
                    "INSERT INTO macro_themes (cluster_key, run_id, label, member_count) "
                    "VALUES (:ck, :rid, :lbl, :n) RETURNING id"
                ),
                {"ck": str(cluster_id), "rid": run_id, "lbl": card.get("title"), "n": len(members)},
            ).scalar_one()

            conn.execute(
                text(
                    "INSERT INTO macro_theme_members (macro_theme_id, insight_id) "
                    "VALUES (:mt, :iid)"
                ),
                [{"mt": mt_id, "iid": m["id"]} for m in members],
            )

            conn.execute(
                text(
                    """
                    INSERT INTO insight_cards (
                      macro_theme_id, title, one_line, detailed,
                      persona_most_affected, primary_barrier, suggested_experiment,
                      confidence, confidence_breakdown, source_counts, unique_authors, prompt_version
                    ) VALUES (
                      :mt, :title, :one_line, :detailed,
                      :persona, :barrier, :experiment,
                      :conf, CAST(:breakdown AS JSONB), CAST(:sources AS JSONB), :authors, :pv
                    )
                    """
                ),
                {
                    "mt": mt_id,
                    "title": (card.get("title") or "Untitled")[:200],
                    "one_line": (card.get("one_line") or "")[:400],
                    "detailed": card.get("detailed") or "",
                    "persona": card.get("persona_most_affected"),
                    "barrier": card.get("primary_barrier"),
                    "experiment": card.get("suggested_experiment"),
                    "conf": conf,
                    "breakdown": json.dumps(breakdown),
                    "sources": json.dumps(source_counts),
                    "authors": unique_authors,
                    "pv": SYNTH_PROMPT_VERSION,
                },
            )
            cards_written += 1

    log.info("synthesize: wrote %d insight cards (run_id=%s)", cards_written, run_id)
    return cards_written


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--eps", type=float, default=0.30)
    ap.add_argument("--min-samples", type=int, default=5)
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args()
    logging.basicConfig(level=args.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    run(args.eps, args.min_samples)
