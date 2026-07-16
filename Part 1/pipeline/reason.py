"""Reasoning layer — the actual decision engine.

Aggregates corpus-level statistics into a "portrait" and gives GPT-4.1 a
sample of 30 representative verbatim quotes, then asks it to reason across the
whole thing to produce 5 non-obvious hypotheses about Zepto's category
exploration problem.

Different from synthesize_v3 in that:
  * synthesize_v3 clusters similar reviews and describes each cluster
  * reason.py sees the WHOLE corpus at once and reasons about the shape of it,
    including what's absent

Persisted to `corpus_hypotheses` table so the dashboard can render them.
"""
from __future__ import annotations

import json
import logging
from collections import Counter
from typing import Any

from sqlalchemy import text

from .openai_client import SYNTHESIZE_MODEL, chat_json
from .settings import load_prompt
from .storage import engine

log = logging.getLogger(__name__)

PROMPT_VERSION = "reason_v1"
_PROMPT = load_prompt("reason_v1.txt")

_ENSURE_TABLE = text(
    """
    CREATE TABLE IF NOT EXISTS corpus_hypotheses (
        id                                 BIGSERIAL PRIMARY KEY,
        top_line_read                      TEXT,
        title                              TEXT NOT NULL,
        claim                              TEXT,
        reasoning                          TEXT,
        grounded_in                        JSONB,
        counter_evidence_that_would_disprove TEXT,
        confidence                         TEXT,
        novelty                            TEXT,
        implication_for_zepto              TEXT,
        interview_probe                    TEXT,
        rank                               INT,
        what_this_corpus_cannot_answer     TEXT,
        recommended_next_data_collection   TEXT,
        model                              TEXT,
        prompt_version                     TEXT,
        run_id                             TEXT,
        created_at                         TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    CREATE INDEX IF NOT EXISTS corpus_hypotheses_run_idx ON corpus_hypotheses(run_id);
    """
)


def _corpus_stats() -> dict[str, Any]:
    stats: dict[str, Any] = {}
    with engine().begin() as conn:
        stats["totals"] = {
            "raw_snippets":         conn.execute(text("SELECT COUNT(*) FROM raw_snippets")).scalar_one(),
            "filtered_snippets":    conn.execute(text("SELECT COUNT(*) FROM filtered_snippets")).scalar_one(),
            "extracted_v1":         conn.execute(text("SELECT COUNT(*) FROM extracted_insights")).scalar_one(),
            "discovery_v2":         conn.execute(text("SELECT COUNT(*) FROM discovery_signals")).scalar_one(),
        }
        stats["by_source_brand"] = [
            {"source": s, "brand": b, "n": n}
            for s, b, n in conn.execute(text(
                "SELECT source, brand, COUNT(*) FROM raw_snippets GROUP BY 1,2 ORDER BY 3 DESC"
            ))
        ]
        stats["intent_distribution"] = dict(conn.execute(text(
            "SELECT intent, COUNT(*) FROM extracted_insights GROUP BY 1 ORDER BY 2 DESC"
        )).all())
        stats["barrier_distribution_v1"] = dict(conn.execute(text(
            "SELECT unnest(themes), COUNT(*) FROM extracted_insights "
            "WHERE intent!='Irrelevant' GROUP BY 1 ORDER BY 2 DESC"
        )).all())
        stats["category_avoiding_named"] = dict(conn.execute(text(
            "SELECT category_avoiding, COUNT(*) FROM extracted_insights "
            "WHERE category_avoiding!='unknown' AND intent!='Irrelevant' GROUP BY 1 ORDER BY 2 DESC"
        )).all())
        stats["category_currently_buying_named"] = dict(conn.execute(text(
            "SELECT category_currently_buying, COUNT(*) FROM extracted_insights "
            "WHERE category_currently_buying!='unknown' AND intent!='Irrelevant' GROUP BY 1 ORDER BY 2 DESC LIMIT 10"
        )).all())
        stats["exploration_signal_v2"] = dict(conn.execute(text(
            "SELECT exploration_signal, COUNT(*) FROM discovery_signals GROUP BY 1 ORDER BY 2 DESC"
        )).all())
        stats["signal_strength_v2"] = dict(conn.execute(text(
            "SELECT signal_strength, COUNT(*) FROM discovery_signals "
            "WHERE exploration_signal!='none' GROUP BY 1 ORDER BY 2 DESC"
        )).all())
        stats["trigger_type_v2"] = dict(conn.execute(text(
            "SELECT trigger_type, COUNT(*) FROM discovery_signals "
            "WHERE exploration_signal!='none' GROUP BY 1 ORDER BY 2 DESC"
        )).all())
        stats["novelty_moments"] = conn.execute(text(
            "SELECT COUNT(*) FROM discovery_signals WHERE novelty_moment=true"
        )).scalar_one()
        stats["kirana_preference_mentions"] = conn.execute(text(
            "SELECT COUNT(*) FROM discovery_signals WHERE kirana_or_specialty_preference=true"
        )).scalar_one()
        stats["persona_distribution"] = dict(conn.execute(text(
            "SELECT user_persona, COUNT(*) FROM extracted_insights "
            "WHERE user_persona!='unknown' AND intent!='Irrelevant' GROUP BY 1 ORDER BY 2 DESC"
        )).all())

        # By-brand intent distribution — is the barrier shape same across QCs?
        brand_rows = list(conn.execute(text("SELECT DISTINCT brand FROM raw_snippets ORDER BY 1")).all())
        stats["intent_by_brand"] = {
            b: dict(conn.execute(text(
                "SELECT intent, COUNT(*) FROM extracted_insights e "
                "JOIN raw_snippets r ON r.id=e.snippet_id "
                "WHERE r.brand = :b GROUP BY 1 ORDER BY 2 DESC"
            ), {"b": b}).all())
            for (b,) in brand_rows
        }

        # Categories mentioned in discovery_signals — what USERS TALK ABOUT
        cats_counter: Counter = Counter()
        for (cats,) in conn.execute(text(
            "SELECT categories_mentioned FROM discovery_signals WHERE exploration_signal!='none'"
        )):
            for c in (cats or []):
                if c and c != "unknown":
                    cats_counter[c] += 1
        stats["categories_mentioned_at_all"] = dict(cats_counter.most_common())

        # Comparison targets — when user is comparing brands, what dimension?
        stats["comparison_snippet_count"] = conn.execute(text(
            "SELECT COUNT(*) FROM discovery_signals WHERE exploration_signal='comparison'"
        )).scalar_one()

        # Category edges (from cooccurrence)
        stats["top_gateway_wants"] = [
            {"from": src, "to": dst, "weight": w}
            for src, dst, w in conn.execute(text(
                "SELECT src, dst, weight FROM category_edges "
                "WHERE edge_type='gateway_want' ORDER BY weight DESC LIMIT 10"
            ))
        ]
        stats["top_co_mentions"] = [
            {"a": a, "b": b, "weight": w}
            for a, b, w in conn.execute(text(
                "SELECT src, dst, weight FROM category_edges "
                "WHERE edge_type='co_mention' ORDER BY weight DESC LIMIT 10"
            ))
        ]

    # Derived signals
    total_extract = stats["totals"]["extracted_v1"] or 1
    stats["derived"] = {
        "pct_exploration_none": round(
            100.0 * (stats["exploration_signal_v2"].get("none", 0)) / (stats["totals"]["discovery_v2"] or 1), 2
        ),
        "pct_irrelevant_extract_v1": round(
            100.0 * (stats["intent_distribution"].get("Irrelevant", 0)) / total_extract, 2
        ),
        "novelty_moments_pct":
            round(100.0 * stats["novelty_moments"] / (stats["totals"]["discovery_v2"] or 1), 3),
        "unique_categories_ever_mentioned": len(stats["categories_mentioned_at_all"]),
        "brands_analyzed": sorted({row["brand"] for row in stats["by_source_brand"]}),
        "sources_analyzed": sorted({row["source"] for row in stats["by_source_brand"]}),
    }
    return stats


def _sample_quotes(n_per_bucket: int = 3) -> list[dict[str, Any]]:
    """30 representative quotes chosen to span:
    - each brand (zepto, blinkit, bigbasket, swiggy_instamart)
    - each source (play_store, reddit, youtube)
    - each intent bucket (Exploration_Blocker, Unmet_Need, Discovery_Request,
      Repeat_Purchase_Habit)
    - Plus 3 from discovery_signals where signal != 'none'
    """
    quotes: list[dict[str, Any]] = []
    with engine().begin() as conn:
        # 3 per brand per intent — deliberately messy sample
        for brand in ("zepto", "blinkit", "bigbasket", "swiggy_instamart"):
            for intent in ("Exploration_Blocker", "Unmet_Need", "Discovery_Request", "Repeat_Purchase_Habit"):
                rows = conn.execute(text(
                    """
                    SELECT r.text, r.source, r.brand, e.intent, e.themes,
                           e.category_currently_buying, e.category_avoiding, e.barrier_summary
                    FROM extracted_insights e
                    JOIN raw_snippets r ON r.id = e.snippet_id
                    WHERE r.brand = :b AND e.intent = :i AND length(r.text) BETWEEN 40 AND 800
                    ORDER BY random()
                    LIMIT 1
                    """
                ), {"b": brand, "i": intent})
                for row in rows:
                    quotes.append(dict(row._mapping))
        # Discovery-signal-rich quotes
        rows = conn.execute(text(
            """
            SELECT r.text, r.source, r.brand, d.exploration_signal, d.signal_strength,
                   d.mental_model, d.categories_mentioned, d.gateway_hint
            FROM discovery_signals d
            JOIN raw_snippets r ON r.id = d.snippet_id
            WHERE d.exploration_signal != 'none' AND length(r.text) BETWEEN 40 AND 800
            ORDER BY random()
            LIMIT 5
            """
        ))
        for row in rows:
            quotes.append(dict(row._mapping))
    # Truncate quotes to 400 chars for token budget
    for q in quotes:
        if "text" in q and isinstance(q["text"], str) and len(q["text"]) > 400:
            q["text"] = q["text"][:400] + "…"
    return quotes[:30]


def _format_quotes(quotes: list[dict]) -> str:
    lines = []
    for i, q in enumerate(quotes, 1):
        header_parts = [f"#{i}", q.get("source", ""), q.get("brand", "")]
        if q.get("intent"):
            header_parts.append(f"intent={q['intent']}")
        if q.get("exploration_signal"):
            header_parts.append(f"signal={q['exploration_signal']}")
        header = " · ".join(str(p) for p in header_parts if p)
        lines.append(f"[{header}]\n{q.get('text','').strip()}\n")
    return "\n".join(lines)


def run(model: str = SYNTHESIZE_MODEL) -> int:
    with engine().begin() as conn:
        conn.execute(_ENSURE_TABLE)

    stats = _corpus_stats()
    quotes = _sample_quotes()
    prompt = (
        _PROMPT
        .replace("{stats_block}", json.dumps(stats, indent=2, default=str))
        .replace("{quotes_block}", _format_quotes(quotes))
    )
    log.info("reason: prompt %d chars, quotes=%d", len(prompt), len(quotes))

    try:
        raw = chat_json(prompt, model=model, temperature=0.4)
    except Exception as e:
        log.error("reason: chat call failed: %s", e)
        return 0

    hypotheses = raw.get("hypotheses") or []
    top_line = raw.get("top_line_read")
    unanswerable = raw.get("what_this_corpus_cannot_answer")
    next_data = raw.get("recommended_next_data_collection")

    if not hypotheses:
        log.warning("reason: model returned no hypotheses; raw=%s", str(raw)[:400])
        return 0

    run_id = f"reason_{stats['totals']['raw_snippets']}"

    to_insert = []
    for i, h in enumerate(hypotheses, 1):
        to_insert.append({
            "top_line_read": top_line,
            "title":         (h.get("title") or "")[:200],
            "claim":         h.get("claim") or "",
            "reasoning":     h.get("reasoning") or "",
            "grounded_in":   json.dumps(h.get("grounded_in") or []),
            "counter":       h.get("counter_evidence_that_would_disprove") or "",
            "confidence":    h.get("confidence") or "medium",
            "novelty":       h.get("novelty") or "obvious",
            "implication":   h.get("implication_for_zepto") or "",
            "interview":     h.get("interview_probe") or "",
            "rank":          i,
            "unanswerable":  unanswerable or "",
            "next_data":     next_data or "",
            "model":         model,
            "pv":            PROMPT_VERSION,
            "run_id":        run_id,
        })

    with engine().begin() as conn:
        conn.execute(text(
            """
            INSERT INTO corpus_hypotheses (
              top_line_read, title, claim, reasoning, grounded_in,
              counter_evidence_that_would_disprove, confidence, novelty,
              implication_for_zepto, interview_probe, rank,
              what_this_corpus_cannot_answer, recommended_next_data_collection,
              model, prompt_version, run_id
            ) VALUES (
              :top_line_read, :title, :claim, :reasoning, CAST(:grounded_in AS JSONB),
              :counter, :confidence, :novelty,
              :implication, :interview, :rank,
              :unanswerable, :next_data,
              :model, :pv, :run_id
            )
            """
        ), to_insert)

    log.info("reason: wrote %d hypotheses (run_id=%s)", len(to_insert), run_id)
    return len(to_insert)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=SYNTHESIZE_MODEL)
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args()
    logging.basicConfig(level=args.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    run(args.model)
