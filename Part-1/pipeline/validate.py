"""Phase D step 2 — Multi-layer insight validation.

Runs 5 automated validation checks per insight:
  1. Evidence Validation      — ≥ 5 evidence rows (already enforced at generation)
  2. Cross-Source Validation  — evidence spans ≥ 2 sources AND ≥ 2 brands
  3. Statistical Validation   — theme has ≥ 20 unique authors AND theme members / total ≥ 0.005
  4. Cluster Quality          — theme intra-cluster mean cosine ≥ 0.70
  5. LLM Critic Agent         — different model reviews insight + 20 supporting + 10 counter-retrieved quotes

Behavioural + Business validation are recorded as `not_evaluated` (no internal
Zepto data available).

The composite Confidence Score is computed and validation_status is set:
  confirmed = all hard checks pass AND critic verdict = pass
  exploratory = one soft check misses OR critic verdict = revise
  shelved = critic verdict = reject OR cross-source check fails hard

Usage:
    python -m pipeline.validate
"""
from __future__ import annotations

import argparse
import json
import logging

import numpy as np
from sklearn.metrics.pairwise import cosine_similarity
from sqlalchemy import text

from .embed import CLASS_NAME, wclient
from .openai_client import SYNTHESIZE_MODEL, chat_json
from .settings import load_prompt
from .storage import engine

log = logging.getLogger(__name__)

CRITIC_PROMPT_VERSION = "critic_v1"
_CRITIC_PROMPT = load_prompt("critic_v1.txt")
import os as _os
TAXONOMY_VERSION = int(_os.environ.get("V2_TAXONOMY_VERSION", "1"))

# Thresholds (from plan)
MIN_EVIDENCE = 5
MIN_UNIQUE_AUTHORS = 20
MIN_THEME_FRACTION = 0.005
MIN_INTRA_CLUSTER_SIM = 0.70


def _fetch_insights_needing_validation() -> list[dict]:
    q = text(
        """
        SELECT i.id, i.theme_id, i.hypothesis, i.one_line, i.detailed,
               i.generator_model, i.confidence_breakdown, t.name AS theme_name
        FROM insights_v2 i
        JOIN themes t ON t.id = i.theme_id
        WHERE i.validation_status = 'exploratory'
          AND i.critic_verdict IS NULL
        """
    )
    with engine().begin() as conn:
        return [dict(r._mapping) for r in conn.execute(q).all()]


def _evidence_rows(insight_id: int) -> list[dict]:
    q = text(
        """
        SELECT ie.snippet_id, ie.kind, r.text, r.source, r.brand, r.author
        FROM insight_evidence ie
        JOIN raw_snippets r ON r.id = ie.snippet_id
        WHERE ie.insight_id = :iid
        """
    )
    with engine().begin() as conn:
        return [dict(r._mapping) for r in conn.execute(q, {"iid": insight_id}).all()]


def _theme_members(theme_id: int) -> list[dict]:
    q = text(
        """
        SELECT rt.snippet_id, r.text, r.source, r.brand, r.author
        FROM review_themes rt
        JOIN raw_snippets r ON r.id = rt.snippet_id
        WHERE rt.theme_id = :tid AND rt.taxonomy_version = :v
        """
    )
    with engine().begin() as conn:
        return [dict(r._mapping) for r in conn.execute(q, {"tid": theme_id, "v": TAXONOMY_VERSION}).all()]


def _total_corpus() -> int:
    with engine().begin() as conn:
        return conn.execute(text(
            "SELECT COUNT(*) FROM snippet_quality WHERE is_spam = false AND dup_of IS NULL"
        )).scalar_one()


def _fetch_vectors(snippet_ids: list[str]) -> dict[str, np.ndarray]:
    from weaviate.classes.query import Filter
    coll = wclient().collections.get(CLASS_NAME)
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
                    n = np.linalg.norm(vec)
                    if n > 0:
                        out[sid] = vec / n
    return out


def _cluster_quality(theme_members: list[dict]) -> float | None:
    """Intra-cluster mean cosine similarity."""
    if len(theme_members) < 2:
        return None
    ids = [m["snippet_id"] for m in theme_members][:200]  # cap for speed
    vecs = _fetch_vectors(ids)
    if len(vecs) < 2:
        return None
    X = np.stack(list(vecs.values()))
    sim = cosine_similarity(X)
    iu = np.triu_indices_from(sim, k=1)
    return float(sim[iu].mean())


def _retrieve_counter_evidence(hypothesis: str, hint: str, exclude_ids: set, n: int = 10) -> list[dict]:
    """Use the hypothesis + counter-evidence hint as a search query to find
    corpus snippets that MIGHT contradict. LLM critic then judges whether they
    actually do."""
    from .openai_client import embed_batch
    query = f"{hypothesis}\n{hint}\n(find quotes that would contradict this or show the opposite)"
    query_vec = embed_batch([query])[0]
    coll = wclient().collections.get(CLASS_NAME)
    from weaviate.classes.query import MetadataQuery
    res = coll.query.near_vector(
        near_vector=query_vec,
        limit=n + len(exclude_ids),
        return_metadata=MetadataQuery(distance=True),
    )
    counter = []
    for o in res.objects:
        sid = o.properties.get("snippet_id")
        if not sid or sid in exclude_ids:
            continue
        with engine().begin() as conn:
            row = conn.execute(text(
                "SELECT text, source, brand FROM raw_snippets WHERE id = :id"
            ), {"id": sid}).first()
        if row:
            counter.append({"snippet_id": sid, "text": row.text, "source": row.source, "brand": row.brand})
        if len(counter) >= n:
            break
    return counter


def _format_evidence(rows: list[dict]) -> str:
    lines = []
    for r in rows:
        txt = (r.get("text") or "").replace("\n", " ").strip()[:300]
        lines.append(f"[id={r.get('snippet_id')}][{r.get('source')}/{r.get('brand')}] {txt}")
    return "\n".join(lines)


def _run_critic(insight: dict, supporting: list[dict], counter: list[dict]) -> dict | None:
    generator_model = insight.get("generator_model") or "deepseek-chat"
    prompt = (_CRITIC_PROMPT
              .replace("{generator_model}", generator_model)
              .replace("{critic_model}", "gpt-4.1")
              .replace("{hypothesis}", insight["hypothesis"] or "")
              .replace("{one_line}", insight["one_line"] or "")
              .replace("{detailed}", insight["detailed"] or "")
              .replace("{supporting_block}", _format_evidence(supporting[:20]))
              .replace("{counter_block}", _format_evidence(counter[:10])))
    try:
        raw = chat_json(prompt, model=SYNTHESIZE_MODEL, temperature=0.2)
    except Exception as e:
        log.warning("critic: failed for insight #%d: %s", insight["id"], str(e)[:200])
        return None
    if raw.get("verdict") not in ("pass", "revise", "reject"):
        return None
    return raw


def _compute_confidence(breakdown: dict) -> float:
    """Composite confidence score, per PDF Section 3.9."""
    score = 0.0
    score += 20 * (1 if breakdown.get("evidence_pass") else 0)
    score += 15 * (1 if breakdown.get("cross_source_pass") else 0)
    score += 15 * (1 if breakdown.get("statistical_pass") else 0)
    score += 15 * (1 if breakdown.get("cluster_quality_pass") else 0)
    # Critic verdict weight
    verdict = breakdown.get("critic_verdict")
    if verdict == "pass":
        score += 25
    elif verdict == "revise":
        score += 12
    # Recency (all our data is fresh; give constant 10 for now)
    score += 10
    return round(score, 2)


def _write_counter_evidence(insight_id: int, counter: list[dict]) -> None:
    if not counter:
        return
    with engine().begin() as conn:
        conn.execute(text(
            "INSERT INTO insight_evidence (insight_id, snippet_id, kind, retrieval_score) "
            "VALUES (:iid, :sid, 'contradicting', 0.5) ON CONFLICT DO NOTHING"
        ), [{"iid": insight_id, "sid": c["snippet_id"]} for c in counter])


def _update_insight(insight_id: int, breakdown: dict, critic: dict | None, status: str, confidence: float) -> None:
    with engine().begin() as conn:
        conn.execute(text(
            """
            UPDATE insights_v2 SET
              validation_status = :status,
              critic_verdict = :cv,
              critic_notes = :cn,
              confidence = :conf,
              confidence_breakdown = CAST(:bd AS JSONB)
            WHERE id = :id
            """
        ), {
            "status": status,
            "cv": (critic or {}).get("verdict"),
            "cn": (critic or {}).get("reasoning"),
            "conf": confidence,
            "bd": json.dumps(breakdown),
            "id": insight_id,
        })


def run() -> int:
    insights = _fetch_insights_needing_validation()
    if not insights:
        log.info("validate: nothing to validate")
        return 0
    total_corpus = _total_corpus()
    log.info("validate: %d insights to check (corpus size=%d)", len(insights), total_corpus)

    n_confirmed, n_explor, n_shelved = 0, 0, 0
    for ins in insights:
        breakdown = dict(ins.get("confidence_breakdown") or {})
        evidence = _evidence_rows(ins["id"])
        supporting = [e for e in evidence if e["kind"] == "supporting"]
        theme_members = _theme_members(ins["theme_id"])

        # 1. Evidence
        breakdown["evidence_pass"] = len(supporting) >= MIN_EVIDENCE

        # 2. Cross-source
        n_sources = len({e["source"] for e in supporting})
        n_brands = len({e["brand"] for e in supporting})
        breakdown["evidence_source_count"] = n_sources
        breakdown["evidence_brand_count"] = n_brands
        breakdown["cross_source_pass"] = n_sources >= 2 and n_brands >= 2

        # 3. Statistical
        unique_authors = len({m["author"] for m in theme_members if m["author"]})
        theme_fraction = len(theme_members) / (total_corpus or 1)
        breakdown["unique_authors"] = unique_authors
        breakdown["theme_fraction"] = round(theme_fraction, 4)
        breakdown["statistical_pass"] = unique_authors >= MIN_UNIQUE_AUTHORS and theme_fraction >= MIN_THEME_FRACTION

        # 4. Cluster quality
        intra = _cluster_quality(theme_members)
        breakdown["intra_cluster_sim"] = round(intra, 3) if intra is not None else None
        breakdown["cluster_quality_pass"] = (intra or 0) >= MIN_INTRA_CLUSTER_SIM

        # 5. Critic (with counter-evidence retrieval)
        exclude_ids = {e["snippet_id"] for e in supporting}
        counter = _retrieve_counter_evidence(
            ins["hypothesis"] or "",
            breakdown.get("counter_evidence_hint") or "",
            exclude_ids,
        )
        _write_counter_evidence(ins["id"], counter)
        critic = _run_critic(ins, supporting, counter)
        breakdown["critic_verdict"] = (critic or {}).get("verdict")
        breakdown["critic_hallucination_flags"] = (critic or {}).get("hallucination_flags", [])
        breakdown["critic_plausible"] = (critic or {}).get("plausible")
        breakdown["contradicting_evidence_found"] = (critic or {}).get("contradicting_evidence_found")

        # Skipped layers
        breakdown["behavioural_validation"] = "not_evaluated"
        breakdown["business_experiment_validation"] = "deferred_to_part_4"

        # Verdict
        cv = (critic or {}).get("verdict")
        if cv == "reject" or (breakdown["cross_source_pass"] is False and n_sources < 1):
            status = "shelved"
            n_shelved += 1
        elif (cv == "pass"
              and breakdown["evidence_pass"]
              and breakdown["cross_source_pass"]
              and breakdown["statistical_pass"]
              and breakdown["cluster_quality_pass"]):
            status = "confirmed"
            n_confirmed += 1
        else:
            status = "exploratory"
            n_explor += 1

        confidence = _compute_confidence(breakdown)
        _update_insight(ins["id"], breakdown, critic, status, confidence)
        log.info("validate: insight #%d [%s] → %s (conf=%.1f, critic=%s)",
                 ins["id"], (ins.get("theme_name") or "")[:40], status, confidence, cv or "n/a")

    log.info("validate: DONE confirmed=%d exploratory=%d shelved=%d", n_confirmed, n_explor, n_shelved)
    return n_confirmed + n_explor + n_shelved


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args()
    logging.basicConfig(level=args.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    run()
