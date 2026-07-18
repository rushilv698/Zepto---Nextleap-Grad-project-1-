"""Per-snippet structured extraction with GPT-4o-mini → extracted_insights.

We only process snippets that passed filter and haven't been extracted at the
current prompt version. Idempotent by (snippet_id, prompt_version).
"""
from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from sqlalchemy import text
from tqdm import tqdm

from .openai_client import EXTRACT_MODEL, chat_json
from .settings import TAXONOMY, load_prompt
from .storage import engine

log = logging.getLogger(__name__)

PROMPT_VERSION = "extract_v1"
_PROMPT = load_prompt("extract_v1.txt")

_INTENTS = {c["name"] for c in TAXONOMY["intent_classes"]}
_BARRIERS = {b["name"] for b in TAXONOMY["barriers"]}
_PERSONAS = set(TAXONOMY["personas"])
_TONES = set(TAXONOMY["emotional_tones"])
_CATEGORIES = set(TAXONOMY["categories"])


def _sanitize(obj: dict) -> dict:
    """Coerce LLM output into the fixed taxonomy — drop bad labels rather than raise."""
    def pick(v, allowed: set, default: str = "unknown") -> str:
        return v if isinstance(v, str) and v in allowed else default

    intent = pick(obj.get("intent"), _INTENTS, "Irrelevant")
    themes = [t for t in (obj.get("themes") or []) if isinstance(t, str) and t in _BARRIERS]
    return {
        "intent": intent,
        "themes": themes,
        "user_persona": pick(obj.get("user_persona"), _PERSONAS),
        "category_currently_buying": pick(obj.get("category_currently_buying"), _CATEGORIES),
        "category_avoiding": pick(obj.get("category_avoiding"), _CATEGORIES),
        "barrier_summary": (obj.get("barrier_summary") or "")[:400],
        "emotional_tone": pick(obj.get("emotional_tone"), _TONES, "indifference"),
        "actionable_quote": bool(obj.get("actionable_quote")),
        "suggested_intervention": (obj.get("suggested_intervention") or "")[:400],
    }


_FETCH_SQL = text(
    """
    SELECT f.snippet_id, r.text
    FROM filtered_snippets f
    JOIN raw_snippets r ON r.id = f.snippet_id
    LEFT JOIN extracted_insights e
      ON e.snippet_id = f.snippet_id AND e.prompt_version = :pv
    WHERE e.id IS NULL
    ORDER BY
      -- Highest-signal sources first, then Zepto brand, then everything else.
      CASE r.source WHEN 'youtube' THEN 0 WHEN 'reddit' THEN 1 ELSE 2 END,
      CASE r.brand WHEN 'zepto' THEN 0 ELSE 1 END,
      f.filtered_at
    LIMIT :lim
    """
)

_INSERT_SQL = text(
    """
    INSERT INTO extracted_insights (
        snippet_id, intent, themes, user_persona,
        category_currently_buying, category_avoiding, barrier_summary,
        emotional_tone, actionable_quote, suggested_intervention,
        prompt_version, model, raw_response
    ) VALUES (
        :snippet_id, :intent, :themes, :user_persona,
        :category_currently_buying, :category_avoiding, :barrier_summary,
        :emotional_tone, :actionable_quote, :suggested_intervention,
        :prompt_version, :model, CAST(:raw_response AS JSONB)
    )
    ON CONFLICT (snippet_id, prompt_version) DO NOTHING
    """
)


def _process_one(snippet_id: str, text_val: str, model: str) -> dict | None:
    prompt = _PROMPT.replace("{text}", text_val[:6000])
    try:
        raw = chat_json(prompt, model=model)
    except Exception as e:
        log.warning("extract failed for %s: %s", snippet_id, str(e)[:200])
        return None
    row = _sanitize(raw)
    row.update(
        snippet_id=snippet_id,
        prompt_version=PROMPT_VERSION,
        model=model,
        raw_response=json.dumps(raw),
    )
    return row


def run(limit: int = 500, model: str = EXTRACT_MODEL, workers: int = 8) -> int:
    with engine().begin() as conn:
        rows = list(conn.execute(_FETCH_SQL, {"pv": PROMPT_VERSION, "lim": limit}))
    if not rows:
        log.info("extract: nothing to do")
        return 0

    log.info("extract: %d snippets to process (model=%s, workers=%d)", len(rows), model, workers)
    to_insert = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_process_one, r.snippet_id, r.text, model): r.snippet_id for r in rows}
        for f in tqdm(as_completed(futures), total=len(futures), desc="extract"):
            row = f.result()
            if row is not None:
                to_insert.append(row)
            # Flush every 200 to avoid one giant transaction at the end.
            if len(to_insert) >= 200:
                with engine().begin() as conn:
                    conn.execute(_INSERT_SQL, to_insert)
                to_insert = []

    if to_insert:
        with engine().begin() as conn:
            conn.execute(_INSERT_SQL, to_insert)
    log.info("extract: done")
    return len(rows)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=500)
    ap.add_argument("--model", default=EXTRACT_MODEL)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args()
    logging.basicConfig(level=args.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    run(args.limit, args.model, args.workers)
