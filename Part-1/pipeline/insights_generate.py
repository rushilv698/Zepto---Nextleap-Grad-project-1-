"""Phase D step 1 — Generate one insight per theme, with evidence links.

For each theme with ≥ MIN_MEMBERS assigned reviews:
  1. Sample 15 highest-quality members (info_value_score DESC).
  2. Pass to DeepSeek with insights_generate_v1 prompt.
  3. Model returns hypothesis + one-line + detailed + evidence-quote ids
     + counter-evidence hint + suggested experiment + interview probe +
     novelty + own confidence.
  4. Row inserted into insights_v2; supporting evidence rows into insight_evidence.

Validation (cross-source, statistical, cluster quality, critic) is a SEPARATE
step run by pipeline/validate_critic.py + pipeline/validate_cross_source.py etc.

Usage:
    python -m pipeline.insights_generate --min-members 8
"""
from __future__ import annotations

import argparse
import json
import logging
import random

from sqlalchemy import text

from .openai_client import EXTRACT_MODEL, chat_json
from .settings import load_prompt
from .storage import engine

log = logging.getLogger(__name__)

PROMPT_VERSION = "insights_generate_v1"
_PROMPT = load_prompt("insights_generate_v1.txt")
import os as _os
TAXONOMY_VERSION = int(_os.environ.get("V2_TAXONOMY_VERSION", "1"))
MIN_MEMBERS = 8


def _themes_to_process() -> list[dict]:
    q = text(
        """
        SELECT t.id, t.name, t.definition, COUNT(rt.snippet_id) AS member_count
        FROM themes t
        JOIN review_themes rt ON rt.theme_id = t.id AND rt.taxonomy_version = :v
        WHERE t.taxonomy_version = :v
          AND t.status IN ('seed', 'promoted')
          AND t.merged_into IS NULL
          AND t.parent_id IS NOT NULL  -- only leaf themes
        GROUP BY t.id, t.name, t.definition
        HAVING COUNT(rt.snippet_id) >= :min_members
        ORDER BY member_count DESC
        """
    )
    with engine().begin() as conn:
        return [dict(r._mapping) for r in conn.execute(q, {"v": TAXONOMY_VERSION, "min_members": MIN_MEMBERS}).all()]


def _sample_members(theme_id: int, n: int = 15) -> list[dict]:
    q = text(
        """
        SELECT rt.snippet_id AS id, r.text, r.source, r.brand, sq.info_value_score
        FROM review_themes rt
        JOIN raw_snippets r ON r.id = rt.snippet_id
        LEFT JOIN snippet_quality sq ON sq.snippet_id = r.id
        WHERE rt.theme_id = :tid AND rt.taxonomy_version = :v
        ORDER BY sq.info_value_score DESC NULLS LAST, rt.similarity DESC NULLS LAST
        LIMIT 40
        """
    )
    with engine().begin() as conn:
        rows = [dict(r._mapping) for r in conn.execute(q, {"tid": theme_id, "v": TAXONOMY_VERSION}).all()]
    # Sample 15 from the top-40 with slight randomness for diversity
    if len(rows) > n:
        rows = rows[: n * 2]
        random.shuffle(rows)
        rows = rows[:n]
    return rows


def _format_quotes(members: list[dict]) -> str:
    lines = []
    for m in members:
        txt = (m["text"] or "").replace("\n", " ").strip()[:400]
        lines.append(f"[id={m['id']}][{m['source']}/{m['brand']}] {txt}")
    return "\n".join(lines)


def _sanitize(raw: dict, valid_ids: set) -> dict | None:
    hyp = (raw.get("hypothesis") or "").strip()
    if not hyp:
        return None
    ev = [i for i in (raw.get("supporting_evidence_ids") or []) if i in valid_ids]
    nov = raw.get("novelty") if raw.get("novelty") in ("obvious", "non_obvious") else "obvious"
    conf_h = raw.get("confidence_in_hypothesis") if raw.get("confidence_in_hypothesis") in ("low", "medium", "high") else "medium"
    return {
        "hypothesis": hyp[:1000],
        "one_line": (raw.get("one_line") or "")[:400],
        "detailed": (raw.get("detailed") or "")[:2000],
        "supporting_evidence_ids": ev,
        "counter_evidence_hint": (raw.get("counter_evidence_hint") or "")[:400],
        "suggested_experiment": (raw.get("suggested_experiment") or "")[:2000],
        "part_2_probe": (raw.get("part_2_probe") or "")[:400],
        "novelty": nov,
        "confidence_in_hypothesis": conf_h,
    }


def _insert_insight(theme_id: int, members: list[dict], parsed: dict, model: str) -> int | None:
    breakdown = {
        "novelty": parsed["novelty"],
        "confidence_in_hypothesis": parsed["confidence_in_hypothesis"],
        "counter_evidence_hint": parsed["counter_evidence_hint"],
        "member_count": len(members),
        "sources": sorted({m["source"] for m in members}),
        "brands": sorted({m["brand"] for m in members}),
    }
    with engine().begin() as conn:
        insight_id = conn.execute(text(
            """
            INSERT INTO insights_v2 (
                theme_id, taxonomy_version, hypothesis, one_line, detailed,
                suggested_experiment, part_2_probe,
                generator_model, generator_prompt_version,
                confidence_breakdown, validation_status
            ) VALUES (
                :tid, :v, :hyp, :ol, :dt,
                :ex, :probe,
                :model, :pv,
                CAST(:bd AS JSONB), 'exploratory'
            ) RETURNING id
            """
        ), {
            "tid": theme_id, "v": TAXONOMY_VERSION,
            "hyp": parsed["hypothesis"], "ol": parsed["one_line"], "dt": parsed["detailed"],
            "ex": parsed["suggested_experiment"], "probe": parsed["part_2_probe"],
            "model": model, "pv": PROMPT_VERSION,
            "bd": json.dumps(breakdown),
        }).scalar_one()
        # Evidence links
        for sid in parsed["supporting_evidence_ids"]:
            conn.execute(text(
                "INSERT INTO insight_evidence (insight_id, snippet_id, kind, retrieval_score) "
                "VALUES (:iid, :sid, 'supporting', 1.0) "
                "ON CONFLICT DO NOTHING"
            ), {"iid": insight_id, "sid": sid})
    return insight_id


def run(min_members: int = MIN_MEMBERS, model: str = EXTRACT_MODEL) -> int:
    global MIN_MEMBERS
    MIN_MEMBERS = min_members
    themes = _themes_to_process()
    if not themes:
        log.info("insights: no themes with ≥ %d members", min_members)
        return 0
    log.info("insights: generating for %d themes (model=%s)", len(themes), model)
    n_written = 0
    for t in themes:
        members = _sample_members(t["id"])
        if len(members) < 3:
            continue
        prompt = (_PROMPT
                  .replace("{theme_name}", t["name"])
                  .replace("{theme_definition}", t["definition"] or "")
                  .replace("{member_count}", str(t["member_count"]))
                  .replace("{sources_str}", ", ".join(sorted({m["source"] for m in members})))
                  .replace("{brands_str}", ", ".join(sorted({m["brand"] for m in members})))
                  .replace("{quotes_block}", _format_quotes(members)))
        try:
            raw = chat_json(prompt, model=model, temperature=0.3)
        except Exception as e:
            log.warning("insights: gen failed for theme '%s': %s", t["name"], str(e)[:200])
            continue
        valid_ids = {m["id"] for m in members}
        parsed = _sanitize(raw, valid_ids)
        if not parsed:
            log.warning("insights: theme '%s' produced no hypothesis", t["name"])
            continue
        iid = _insert_insight(t["id"], members, parsed, model)
        n_written += 1
        log.info("insights: theme '%s' → insight #%d (evidence=%d, novelty=%s, conf=%s)",
                 t["name"], iid, len(parsed["supporting_evidence_ids"]),
                 parsed["novelty"], parsed["confidence_in_hypothesis"])
    log.info("insights: DONE wrote=%d", n_written)
    return n_written


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-members", type=int, default=MIN_MEMBERS)
    ap.add_argument("--model", default=EXTRACT_MODEL)
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args()
    logging.basicConfig(level=args.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    run(args.min_members, args.model)
