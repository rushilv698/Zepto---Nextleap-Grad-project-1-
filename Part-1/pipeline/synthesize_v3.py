"""Phase 2c re-synthesis with discovery signal + counter-evidence.

Differences from pipeline/synthesize.py:
  1. Cluster material includes discovery_signals (exploration_signal, novelty_moment,
     categories_mentioned, mental_model, gateway_hint, kirana_preference).
  2. Uses synthesize_v3.txt which enforces adversarial thinking + counter-evidence.
  3. Emits Part-2 interview prompts per card so the researcher can walk into
     interviews with a specific probe list.
  4. Cards are stored in `insight_cards_v3` (separate table so we can compare).
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

SYNTH_PROMPT_VERSION = "synthesize_v3"
_PROMPT = load_prompt("synthesize_v3.txt")

_VALID_PERSONAS = {"busy_professional", "homemaker", "student", "new_mover", "senior", "unknown"}
_VALID_BARRIERS = {"trust_deficit", "price_sensitivity", "lack_awareness", "no_urgent_need",
                   "decision_paralysis", "bad_past_experience", "discovery_UI",
                   "information_gap", "habit_loop"}
_ACTIVE_INTENTS = ("Exploration_Blocker", "Repeat_Purchase_Habit", "Discovery_Request", "Unmet_Need")


_ENSURE_CARDS_V3 = text(
    """
    CREATE TABLE IF NOT EXISTS insight_cards_v3 (
        id                       BIGSERIAL PRIMARY KEY,
        macro_theme_id           BIGINT REFERENCES macro_themes(id) ON DELETE SET NULL,
        title                    TEXT NOT NULL,
        hypothesis               TEXT NOT NULL,
        detailed                 TEXT,
        persona_most_affected    TEXT,
        primary_barrier          TEXT,
        supporting_evidence      TEXT,
        counter_evidence_check   TEXT,
        confidence_in_hypothesis TEXT,
        suggested_experiment     TEXT,
        part_2_interview_prompts JSONB,
        confidence               NUMERIC(5,2),
        confidence_breakdown     JSONB,
        source_counts            JSONB,
        brand_counts             JSONB,
        discovery_breakdown      JSONB,
        unique_authors           INT,
        created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        prompt_version           TEXT
    );
    CREATE INDEX IF NOT EXISTS insight_cards_v3_confidence_idx ON insight_cards_v3(confidence DESC);
    """
)


def _load_insights() -> list[dict]:
    """Pull all extracted_insights with active intents, joined with discovery
    signals (if any) and brand/source."""
    q = text(
        """
        SELECT e.id, e.snippet_id, e.intent, e.themes, e.user_persona,
               e.category_currently_buying, e.category_avoiding, e.barrier_summary,
               e.emotional_tone,
               r.source, r.brand, r.author,
               d.exploration_signal, d.signal_strength, d.mental_model,
               d.categories_mentioned, d.trigger_type, d.gateway_hint,
               d.novelty_moment, d.kirana_or_specialty_preference
        FROM extracted_insights e
        JOIN raw_snippets r ON r.id = e.snippet_id
        LEFT JOIN discovery_signals d
          ON d.snippet_id = e.snippet_id AND d.prompt_version = 'extract_v2_discovery'
        WHERE e.prompt_version = :pv AND e.intent = ANY(:intents)
              AND e.barrier_summary IS NOT NULL AND length(e.barrier_summary) > 10
        """
    )
    with engine().begin() as conn:
        return [dict(r._mapping) for r in conn.execute(q, {"pv": EXTRACT_PROMPT_VERSION, "intents": list(_ACTIVE_INTENTS)})]


def _load_vectors(snippet_ids: list[str]) -> dict[str, list[float]]:
    from weaviate.classes.query import Filter
    coll = wclient().collections.get(CLASS_NAME)
    out: dict[str, list[float]] = {}
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


def _cluster(vectors: np.ndarray, eps: float = 0.32, min_samples: int = 4) -> np.ndarray:
    return DBSCAN(eps=eps, min_samples=min_samples, metric="cosine").fit_predict(vectors)


def _dominant(values: list[str]) -> str:
    c = Counter(v for v in values if v and v != "unknown" and v != "none")
    return c.most_common(1)[0][0] if c else "unknown"


def _discovery_breakdown(members: list[dict]) -> dict:
    signals = [m.get("exploration_signal") or "none" for m in members]
    return dict(Counter(signals))


def _synthesize_card(members: list[dict]) -> dict | None:
    summaries = []
    for m in members[:25]:
        line = f"- [{m['source']}/{m['brand']}] {m['barrier_summary']}"
        if m.get("mental_model"):
            line += f"  |  MM: {m['mental_model']}"
        if m.get("gateway_hint"):
            line += f"  |  GW: {m['gateway_hint']}"
        summaries.append(line)
    if len(summaries) < 3:
        return None

    sources = sorted({m["source"] for m in members})
    brands  = sorted({m["brand"]  for m in members})
    persona = _dominant([m["user_persona"] for m in members])
    pair    = _dominant([f"{m['category_currently_buying']}→{m['category_avoiding']}" for m in members])
    discovery_break = _discovery_breakdown(members)

    prompt = (
        _PROMPT
        .replace("{n}", str(len(members)))
        .replace("{sources_str}", ", ".join(sources))
        .replace("{brands_str}",  ", ".join(brands))
        .replace("{dominant_persona}", persona)
        .replace("{dominant_pair}",    pair)
        .replace("{discovery_breakdown}", json.dumps(discovery_break))
        .replace("{summaries_block}", "\n".join(summaries))
    )
    try:
        return chat_json(prompt, model=SYNTHESIZE_MODEL, temperature=0.3)
    except Exception as e:
        log.warning("synth v3 failed: %s", str(e)[:200])
        return None


def _sanitize_card(card: dict, member_barriers: list[str], member_personas: list[str]) -> dict:
    p = card.get("persona_most_affected")
    if p not in _VALID_PERSONAS:
        pool = [x for x in member_personas if x in _VALID_PERSONAS and x != "unknown"]
        p = Counter(pool).most_common(1)[0][0] if pool else "unknown"
    b = card.get("primary_barrier")
    if b not in _VALID_BARRIERS:
        pool = [x for x in member_barriers if x in _VALID_BARRIERS]
        b = Counter(pool).most_common(1)[0][0] if pool else "trust_deficit"
    card["persona_most_affected"] = p
    card["primary_barrier"] = b
    conf = card.get("confidence_in_hypothesis")
    if conf not in {"low", "medium", "high"}:
        card["confidence_in_hypothesis"] = "medium"
    prompts = card.get("part_2_interview_prompts")
    if not isinstance(prompts, list):
        card["part_2_interview_prompts"] = []
    return card


def run(eps: float = 0.32, min_samples: int = 4) -> int:
    with engine().begin() as conn:
        conn.execute(_ENSURE_CARDS_V3)

    insights = _load_insights()
    if len(insights) < 20:
        log.warning("synth v3: only %d insights", len(insights))
        return 0

    vec_map = _load_vectors([i["snippet_id"] for i in insights])
    insights = [i for i in insights if i["snippet_id"] in vec_map]
    if not insights:
        log.warning("synth v3: no vectors")
        return 0

    X = np.array([vec_map[i["snippet_id"]] for i in insights], dtype=np.float32)
    labels = _cluster(X, eps=eps, min_samples=min_samples)
    n_clusters = len(set(labels) - {-1})
    log.info("synth v3: %d insights → %d clusters", len(insights), n_clusters)

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
            brand_counts  = dict(Counter(m["brand"]  for m in members))
            unique_authors = len({m["author"] for m in members if m["author"]})
            tones = [m["emotional_tone"] for m in members if m["emotional_tone"]]
            conf, breakdown = compute_confidence(
                source_counts=source_counts,
                unique_authors=unique_authors,
                tones=tones,
                intra_cluster_cosine_mean=intra,
            )

            mt_id = conn.execute(
                text("INSERT INTO macro_themes (cluster_key, run_id, label, member_count) "
                     "VALUES (:ck, :rid, :lbl, :n) RETURNING id"),
                {"ck": str(cluster_id), "rid": run_id, "lbl": card.get("title"), "n": len(members)},
            ).scalar_one()

            conn.execute(
                text("INSERT INTO macro_theme_members (macro_theme_id, insight_id) "
                     "VALUES (:mt, :iid)"),
                [{"mt": mt_id, "iid": m["id"]} for m in members],
            )

            conn.execute(
                text("""
                    INSERT INTO insight_cards_v3 (
                        macro_theme_id, title, hypothesis, detailed,
                        persona_most_affected, primary_barrier,
                        supporting_evidence, counter_evidence_check,
                        confidence_in_hypothesis, suggested_experiment,
                        part_2_interview_prompts,
                        confidence, confidence_breakdown, source_counts,
                        brand_counts, discovery_breakdown, unique_authors, prompt_version
                    ) VALUES (
                        :mt, :title, :hypothesis, :detailed,
                        :persona, :barrier,
                        :support, :counter,
                        :conf_h, :experiment,
                        CAST(:prompts AS JSONB),
                        :conf, CAST(:breakdown AS JSONB), CAST(:sources AS JSONB),
                        CAST(:brands AS JSONB), CAST(:disc AS JSONB), :authors, :pv
                    )
                """),
                {
                    "mt":        mt_id,
                    "title":     (card.get("title") or "Untitled")[:200],
                    "hypothesis":(card.get("hypothesis") or "")[:600],
                    "detailed":   card.get("detailed") or "",
                    "persona":    card.get("persona_most_affected"),
                    "barrier":    card.get("primary_barrier"),
                    "support":    card.get("supporting_evidence") or "",
                    "counter":    card.get("counter_evidence_check") or "",
                    "conf_h":     card.get("confidence_in_hypothesis"),
                    "experiment": card.get("suggested_experiment") or "",
                    "prompts":    json.dumps(card.get("part_2_interview_prompts") or []),
                    "conf":       conf,
                    "breakdown":  json.dumps(breakdown),
                    "sources":    json.dumps(source_counts),
                    "brands":     json.dumps(brand_counts),
                    "disc":       json.dumps(_discovery_breakdown(members)),
                    "authors":    unique_authors,
                    "pv":         SYNTH_PROMPT_VERSION,
                },
            )
            cards_written += 1

    log.info("synth v3: wrote %d cards (run_id=%s)", cards_written, run_id)
    return cards_written


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--eps", type=float, default=0.32)
    ap.add_argument("--min-samples", type=int, default=4)
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args()
    logging.basicConfig(level=args.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    run(args.eps, args.min_samples)
