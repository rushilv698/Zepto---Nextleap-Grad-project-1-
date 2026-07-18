"""Second-pass extraction focused on discovery / exploration signal.

Runs on:
  1. Every insight that first pass labeled as Exploration_Blocker, Discovery_Request,
     Unmet_Need, or Repeat_Purchase_Habit.
  2. Every YouTube / Reddit snippet regardless of first-pass label (because
     first pass didn't run on new sources yet).

Uses a specialized prompt (extract_v2_discovery.txt) that's conservative — it
returns exploration_signal=none for pure operational complaints, which
counteracts the first pass's tendency to stamp trust_deficit on everything.
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

PROMPT_VERSION = "extract_v2_discovery"
_PROMPT = load_prompt("extract_v2_discovery.txt")

_SIGNALS = {"blocker", "trigger", "request", "comparison", "none"}
_STRENGTHS = {"strong", "moderate", "weak"}
_TRIGGERS = {"banner_ad", "search", "recommendation", "word_of_mouth", "offer_discount", "need_arose", "none"}
_CATEGORIES = set(TAXONOMY["categories"])


def _sanitize(obj: dict) -> dict:
    def pick(v, allowed: set, default: str) -> str:
        return v if isinstance(v, str) and v in allowed else default

    signal = pick(obj.get("exploration_signal"), _SIGNALS, "none")
    if signal == "none":
        return {
            "exploration_signal": "none",
            "signal_strength": "weak",
            "mental_model": "",
            "categories_mentioned": [],
            "trigger_type": "none",
            "gateway_hint": "",
            "novelty_moment": False,
            "kirana_or_specialty_preference": False,
        }
    cats = [c for c in (obj.get("categories_mentioned") or []) if isinstance(c, str) and c in _CATEGORIES]
    return {
        "exploration_signal": signal,
        "signal_strength": pick(obj.get("signal_strength"), _STRENGTHS, "weak"),
        "mental_model": (obj.get("mental_model") or "")[:400],
        "categories_mentioned": cats,
        "trigger_type": pick(obj.get("trigger_type"), _TRIGGERS, "none"),
        "gateway_hint": (obj.get("gateway_hint") or "")[:400],
        "novelty_moment": bool(obj.get("novelty_moment")),
        "kirana_or_specialty_preference": bool(obj.get("kirana_or_specialty_preference")),
    }


_FETCH_SQL = text(
    """
    SELECT r.id AS snippet_id, r.text, r.source, r.brand
    FROM raw_snippets r
    JOIN filtered_snippets f ON f.snippet_id = r.id
    LEFT JOIN discovery_signals d
      ON d.snippet_id = r.id AND d.prompt_version = :pv
    WHERE d.id IS NULL
    ORDER BY
      CASE r.source WHEN 'youtube' THEN 0 WHEN 'reddit' THEN 1 ELSE 2 END,  -- prioritize social
      r.ingested_at
    LIMIT :lim
    """
)

_INSERT_SQL = text(
    """
    INSERT INTO discovery_signals (
        snippet_id, exploration_signal, signal_strength, mental_model,
        categories_mentioned, trigger_type, gateway_hint,
        novelty_moment, kirana_or_specialty_preference,
        prompt_version, model
    ) VALUES (
        :snippet_id, :exploration_signal, :signal_strength, :mental_model,
        :categories_mentioned, :trigger_type, :gateway_hint,
        :novelty_moment, :kirana_or_specialty_preference,
        :prompt_version, :model
    ) ON CONFLICT (snippet_id, prompt_version) DO NOTHING
    """
)


def _process_one(snippet_id: str, text_val: str, model: str) -> dict | None:
    prompt = _PROMPT.replace("{text}", text_val[:6000])
    try:
        raw = chat_json(prompt, model=model)
    except Exception as e:
        log.warning("discovery extract failed for %s: %s", snippet_id, str(e)[:200])
        return None
    row = _sanitize(raw)
    row.update(snippet_id=snippet_id, prompt_version=PROMPT_VERSION, model=model)
    return row


def run(limit: int = 1000, model: str = EXTRACT_MODEL, workers: int = 8) -> int:
    with engine().begin() as conn:
        rows = list(conn.execute(_FETCH_SQL, {"pv": PROMPT_VERSION, "lim": limit}))
    if not rows:
        log.info("extract_discovery: nothing to do")
        return 0

    log.info("extract_discovery: %d snippets (model=%s, workers=%d)", len(rows), model, workers)
    to_insert = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_process_one, r.snippet_id, r.text, model): r.snippet_id for r in rows}
        for f in tqdm(as_completed(futures), total=len(futures), desc="discovery"):
            row = f.result()
            if row is not None:
                to_insert.append(row)
            if len(to_insert) >= 200:
                with engine().begin() as conn:
                    conn.execute(_INSERT_SQL, to_insert)
                to_insert = []
    if to_insert:
        with engine().begin() as conn:
            conn.execute(_INSERT_SQL, to_insert)
    log.info("extract_discovery: done processing %d", len(rows))
    return len(rows)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=1000)
    ap.add_argument("--model", default=EXTRACT_MODEL)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args()
    logging.basicConfig(level=args.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    run(args.limit, args.model, args.workers)
