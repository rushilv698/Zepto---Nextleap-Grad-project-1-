"""Composite Information Value Score (PDF Section 1.7).

score = 0.30·specificity_norm
      + 0.20·novelty
      + 0.20·behavioural_weight
      + 0.15·clarity_norm
      + 0.15·actionability_norm

Where:
  - {specificity,clarity,actionability}_norm = (score - 1) / 4  → [0,1]
  - behavioural_weight  = fraction of behaviour_flags true → [0,1]
  - novelty             = 1 - (max cosine sim of this snippet to any promoted
                          theme centroid). Before any themes are promoted, novelty
                          defaults to 0.5 (neutral).

Output multiplied by 100 and stored as info_value_score in snippet_quality.

Runs after filter_language + dedupe + filter_llm.

Usage:
    python -m pipeline.info_value
"""
from __future__ import annotations

import argparse
import json
import logging

from sqlalchemy import text
from tqdm import tqdm

from .storage import engine

log = logging.getLogger(__name__)


_BEHAVIOUR_KEYS = (
    "describes_routine", "describes_repeat_purchase", "describes_exploration",
    "describes_trust", "describes_hesitation", "describes_price_sensitivity",
    "describes_decision_process",
)


def _themes_table_exists() -> bool:
    with engine().begin() as conn:
        return bool(conn.execute(text(
            "SELECT 1 FROM information_schema.tables WHERE table_name='themes'"
        )).first())


def _novelty_default() -> float:
    # Before themes exist, novelty is neutral.
    return 0.5 if not _themes_table_exists() else 0.5   # placeholder; theme-aware novelty added in Phase C


def _compute(row) -> float:
    bf = row.behaviour_flags or {}
    if isinstance(bf, str):
        try:
            bf = json.loads(bf)
        except Exception:
            bf = {}
    n_true = sum(1 for k in _BEHAVIOUR_KEYS if bf.get(k))
    behavioural = n_true / len(_BEHAVIOUR_KEYS)
    spec = ((row.specificity or 3) - 1) / 4
    clar = ((row.clarity or 3) - 1) / 4
    act  = ((row.actionability or 3) - 1) / 4
    novelty = _novelty_default()
    score = 100.0 * (
        0.30 * spec +
        0.20 * novelty +
        0.20 * behavioural +
        0.15 * clar +
        0.15 * act
    )
    return round(score, 2)


def run(batch: int = 5000) -> int:
    q = text(
        """
        SELECT snippet_id, specificity, clarity, actionability, behaviour_flags, novelty
        FROM snippet_quality
        WHERE is_spam IS NOT NULL AND (info_value_score IS NULL OR updated_at > NOW() - interval '10 minutes')
        LIMIT :lim
        """
    )
    total = 0
    while True:
        with engine().begin() as conn:
            rows = list(conn.execute(q, {"lim": batch}))
        if not rows:
            break
        updates = [{"sid": r.snippet_id, "score": _compute(r)} for r in rows]
        with engine().begin() as conn:
            conn.execute(text(
                "UPDATE snippet_quality SET info_value_score = :score, updated_at = NOW() "
                "WHERE snippet_id = :sid"
            ), updates)
        total += len(rows)
        log.info("info_value: scored %d (running %d)", len(rows), total)
        if len(rows) < batch:
            break
    log.info("info_value: DONE total=%d", total)
    return total


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", type=int, default=5000)
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args()
    logging.basicConfig(level=args.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    run(args.batch)
