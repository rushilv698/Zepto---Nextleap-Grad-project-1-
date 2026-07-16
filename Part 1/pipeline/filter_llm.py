"""Consolidated LLM filter — 5 layers in one call per snippet.

Combines PDF Sections 1.2 (Spam), 1.3 (Relevance), 1.5 (Behaviour),
1.6 (Specificity), and parts of 1.7 (Info Value inputs: clarity + actionability)
into a single DeepSeek call per snippet. This is ~5× cheaper than calling
each layer separately.

Writes results to snippet_quality (is_spam, spam_kind, is_relevant,
behaviour_flags, specificity, clarity, actionability). info_value composite
is computed later by pipeline.info_value.

Skips snippets already marked as dup_of (they inherit their canonical's rating).

Also skips very short snippets (< 12 chars) as auto-spam and very obvious
patterns via cheap regex pre-check.

Usage:
    python -m pipeline.filter_llm --limit 20000 --workers 12
"""
from __future__ import annotations

import argparse
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

from sqlalchemy import text
from tqdm import tqdm

from .openai_client import EXTRACT_MODEL, chat_json
from .settings import load_prompt
from .storage import engine

log = logging.getLogger(__name__)

PROMPT_VERSION = "filter_llm_v1"
_PROMPT = load_prompt("filter_llm_v1.txt")

# Cheap regex fast-path spam detection (skip LLM for these)
_HARD_SPAM_PATTERNS = [
    re.compile(r"telegram\.me/|t\.me/|@[a-z0-9_]+xxx", re.I),
    re.compile(r"whatsapp.{0,20}\+?\d{10,}", re.I),
    re.compile(r"crypto|bitcoin|forex|earn.{0,10}dollar", re.I),
    re.compile(r"^\s*(good|nice|great|bad|worst|best)\s*(app)?\s*[.!👍👎]{0,3}\s*$", re.I),
    re.compile(r"referr?al code|use code|promo code", re.I),
]


def _hard_spam(t: str) -> str | None:
    """Return spam_kind if hard-matched, else None."""
    if len(t.strip()) < 12:
        return "generic_ad"
    for p in _HARD_SPAM_PATTERNS:
        if p.search(t):
            return "coupon" if "code" in p.pattern else "bot"
    return None


_FETCH = text(
    """
    SELECT r.id, r.text
    FROM raw_snippets r
    LEFT JOIN snippet_quality sq ON sq.snippet_id = r.id
    WHERE (sq.snippet_id IS NULL OR sq.is_spam IS NULL)
      AND (sq.dup_of IS NULL OR sq.snippet_id IS NULL)   -- skip dupes
    ORDER BY r.ingested_at
    LIMIT :lim
    """
)

_UPSERT = text(
    """
    INSERT INTO snippet_quality (
        snippet_id, is_spam, spam_kind, is_relevant,
        behaviour_flags, specificity, clarity, actionability, updated_at
    ) VALUES (
        :sid, :is_spam, :spam_kind, :is_relevant,
        CAST(:behaviour_flags AS JSONB), :specificity, :clarity, :actionability, NOW()
    ) ON CONFLICT (snippet_id) DO UPDATE SET
        is_spam=EXCLUDED.is_spam,
        spam_kind=EXCLUDED.spam_kind,
        is_relevant=EXCLUDED.is_relevant,
        behaviour_flags=EXCLUDED.behaviour_flags,
        specificity=EXCLUDED.specificity,
        clarity=EXCLUDED.clarity,
        actionability=EXCLUDED.actionability,
        updated_at=NOW()
    """
)

_ALLOWED_SPAM_KINDS = {"coupon", "bot", "nsfw", "generic_ad", "crypto", "human_but_offtopic", "none"}


def _sanitize(obj: dict) -> dict:
    bf_raw = obj.get("behaviour_flags") or {}
    bf = {
        k: bool(bf_raw.get(k, False))
        for k in ("describes_routine", "describes_repeat_purchase", "describes_exploration",
                  "describes_trust", "describes_hesitation", "describes_price_sensitivity",
                  "describes_decision_process")
    }
    def _int(v, default=3):
        try:
            i = int(v)
            return max(1, min(5, i))
        except Exception:
            return default
    sk = obj.get("spam_kind", "none")
    if sk not in _ALLOWED_SPAM_KINDS:
        sk = "none"
    return {
        "is_spam": bool(obj.get("is_spam", False)),
        "spam_kind": sk,
        "is_relevant": bool(obj.get("is_relevant", True)),
        "behaviour_flags": json.dumps(bf),
        "specificity": _int(obj.get("specificity")),
        "clarity": _int(obj.get("clarity")),
        "actionability": _int(obj.get("actionability")),
    }


def _process_one(sid: str, text_val: str) -> dict | None:
    # Fast-path hard spam — skip LLM
    hs = _hard_spam(text_val or "")
    if hs:
        return {
            "sid": sid,
            "is_spam": True,
            "spam_kind": hs,
            "is_relevant": False,
            "behaviour_flags": json.dumps({k: False for k in (
                "describes_routine", "describes_repeat_purchase", "describes_exploration",
                "describes_trust", "describes_hesitation", "describes_price_sensitivity",
                "describes_decision_process")}),
            "specificity": 1,
            "clarity": 1,
            "actionability": 1,
        }
    prompt = _PROMPT.replace("{text}", (text_val or "")[:5000])
    try:
        raw = chat_json(prompt, model=EXTRACT_MODEL)
    except Exception as e:
        log.warning("filter_llm failed for %s: %s", sid, str(e)[:200])
        return None
    row = _sanitize(raw)
    row["sid"] = sid
    return row


def run(limit: int = 20000, workers: int = 12) -> int:
    with engine().begin() as conn:
        rows = list(conn.execute(_FETCH, {"lim": limit}))
    if not rows:
        log.info("filter_llm: nothing to do")
        return 0
    log.info("filter_llm: %d snippets (workers=%d)", len(rows), workers)
    to_insert: list[dict] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_process_one, r.id, r.text): r.id for r in rows}
        for f in tqdm(as_completed(futures), total=len(futures), desc="filter_llm"):
            row = f.result()
            if row is not None:
                to_insert.append(row)
            if len(to_insert) >= 200:
                with engine().begin() as conn:
                    conn.execute(_UPSERT, to_insert)
                to_insert = []
    if to_insert:
        with engine().begin() as conn:
            conn.execute(_UPSERT, to_insert)
    log.info("filter_llm: done processing %d", len(rows))
    return len(rows)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=20000)
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args()
    logging.basicConfig(level=args.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    run(args.limit, args.workers)
