"""Export dashboard-needed tables to Parquet files for the Streamlit Cloud demo.

Reads from local Postgres, writes to Part 1/demo_data/*.parquet. Only exports
the tables + columns the dashboard actually reads — keeps file size small
(<20MB total for all tables).

The dashboard uses these files as a fallback when Postgres is unreachable
(i.e. when running on Streamlit Community Cloud).

Usage:
    python -m pipeline.export_demo
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

import pandas as pd
from sqlalchemy import text

from .settings import ROOT
from .storage import engine

log = logging.getLogger(__name__)

DEMO_DIR = ROOT / "demo_data"


# Only what the dashboard reads. If you add a new tab, add its table here.
_EXPORTS = {
    # --- v1 ---
    "insight_cards": """
        SELECT id, title, one_line, detailed, persona_most_affected, primary_barrier,
               suggested_experiment, confidence, confidence_breakdown, source_counts,
               unique_authors, created_at
        FROM insight_cards
    """,
    # --- v3 ---
    "insight_cards_v3": """
        SELECT id, title, hypothesis, detailed, persona_most_affected, primary_barrier,
               supporting_evidence, counter_evidence_check, confidence_in_hypothesis,
               suggested_experiment, part_2_interview_prompts, confidence,
               source_counts, brand_counts, discovery_breakdown, unique_authors
        FROM insight_cards_v3
    """,
    # --- Reasoning layer ---
    "corpus_hypotheses": """
        SELECT id, top_line_read, title, claim, reasoning, grounded_in,
               counter_evidence_that_would_disprove, confidence, novelty,
               implication_for_zepto, interview_probe, rank,
               what_this_corpus_cannot_answer, recommended_next_data_collection
        FROM corpus_hypotheses
    """,
    # --- v2 taxonomy ---
    "themes": """
        SELECT id, name, definition, status, parent_id, taxonomy_version, merged_into
        FROM themes
    """,
    "review_themes": """
        SELECT snippet_id, theme_id, similarity, assigned_by, taxonomy_version
        FROM review_themes
    """,
    # --- v2 insights ---
    "insights_v2": """
        SELECT i.id, i.theme_id, t.name AS theme, i.taxonomy_version,
               i.hypothesis, i.one_line, i.detailed, i.suggested_experiment,
               i.part_2_probe, i.confidence, i.validation_status,
               i.critic_verdict, i.critic_notes, i.confidence_breakdown
        FROM insights_v2 i LEFT JOIN themes t ON t.id = i.theme_id
    """,
    # --- filtration state ---
    "snippet_quality": """
        SELECT snippet_id, lang, is_spam, is_relevant, dup_of, info_value_score,
               is_expansion_relevant
        FROM snippet_quality
    """,
    # --- extracted structured insights (feeds Strategic Q&A tab) ---
    "extracted_insights": """
        SELECT e.id, e.snippet_id, e.intent, e.themes, e.user_persona,
               e.category_currently_buying, e.category_avoiding, e.barrier_summary,
               e.emotional_tone, e.actionable_quote,
               r.text, r.source, r.brand, r.posted_at
        FROM extracted_insights e
        JOIN raw_snippets r ON r.id = e.snippet_id
        WHERE e.intent != 'Irrelevant'
        LIMIT 5000
    """,
    # --- raw snippet counts for header metrics ---
    "raw_counts": """
        SELECT source, brand, COUNT(*) AS n FROM raw_snippets GROUP BY 1,2
    """,
    "raw_totals": """
        SELECT
          (SELECT COUNT(*) FROM raw_snippets) AS raw,
          (SELECT COUNT(*) FROM filtered_snippets) AS filtered,
          (SELECT COUNT(*) FROM extracted_insights) AS extracted,
          (SELECT COUNT(*) FROM insight_cards) AS cards
    """,
}


def run() -> int:
    DEMO_DIR.mkdir(parents=True, exist_ok=True)
    total_rows = 0
    with engine().begin() as conn:
        for name, sql in _EXPORTS.items():
            try:
                df = pd.read_sql(text(sql), conn)
            except Exception as e:
                log.warning("export[%s] SKIPPED: %s", name, str(e)[:200])
                continue
            # Coerce JSONB objects (dicts/lists) to json strings for parquet.
            for col in df.columns:
                if df[col].dtype == "object" and len(df) > 0:
                    sample = df[col].dropna().head(1)
                    if len(sample) and isinstance(sample.iloc[0], (dict, list)):
                        import json as _json
                        df[col] = df[col].apply(
                            lambda v: _json.dumps(v, default=str) if isinstance(v, (dict, list)) else v
                        )
            out = DEMO_DIR / f"{name}.parquet"
            df.to_parquet(out, index=False)
            log.info("export[%s]: %d rows → %s", name, len(df), out.name)
            total_rows += len(df)
    log.info("export: DONE. total rows = %d, files in %s", total_rows, DEMO_DIR)
    return total_rows


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args()
    logging.basicConfig(level=args.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    run()
