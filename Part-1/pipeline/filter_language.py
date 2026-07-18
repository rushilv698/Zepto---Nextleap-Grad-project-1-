"""Layer 1 — Language detection + text normalization.

Uses fasttext-langdetect (already installed). Keeps en / hi / hi-en; other
languages are still stored but flagged for down-weighting. Also stores a
normalized text field (lowercase, whitespace collapsed, brand names canonical).

No LLM cost.

Usage:
    python -m pipeline.filter_language
"""
from __future__ import annotations

import argparse
import logging
import re

from sqlalchemy import text
from tqdm import tqdm

from .storage import engine

log = logging.getLogger(__name__)

# Brand canonicalization (variants → canonical)
_BRAND_CANONICALS = {
    r"\bblink\s*it\b": "blinkit",
    r"\bswiggy\s*instamart\b": "swiggy_instamart",
    r"\binsta\s*mart\b": "swiggy_instamart",
    r"\bbig\s*basket\b": "bigbasket",
    r"\bz\s*epto\b": "zepto",
}

_WS = re.compile(r"\s+")


def _normalize(t: str) -> str:
    if not t:
        return ""
    t = t.lower()
    for pat, repl in _BRAND_CANONICALS.items():
        t = re.sub(pat, repl, t)
    t = _WS.sub(" ", t).strip()
    return t


def _detect_lang(text_val: str) -> str:
    try:
        from ftlangdetect import detect
        return detect(text_val[:800], low_memory=True).get("lang", "unknown")
    except Exception:
        return "unknown"


_ENSURE_TABLE = text(
    """
    CREATE TABLE IF NOT EXISTS snippet_quality (
        snippet_id       TEXT PRIMARY KEY REFERENCES raw_snippets(id) ON DELETE CASCADE,
        lang             TEXT,
        is_spam          BOOLEAN,
        spam_kind        TEXT,
        is_relevant      BOOLEAN,
        dup_of           TEXT REFERENCES raw_snippets(id) ON DELETE SET NULL,
        behaviour_flags  JSONB,
        specificity      INT,
        clarity          INT,
        actionability    INT,
        novelty          NUMERIC(4,3),
        info_value_score NUMERIC(5,2),
        weight_recency   NUMERIC(5,3),
        weight_region    TEXT,
        text_normalized_v2 TEXT,
        updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    """
)


def run(batch: int = 2000) -> int:
    with engine().begin() as conn:
        conn.execute(_ENSURE_TABLE)
        # Add column if it wasn't there from earlier version
        conn.execute(text("ALTER TABLE snippet_quality ADD COLUMN IF NOT EXISTS text_normalized_v2 TEXT"))

    q_pending = text(
        """
        SELECT r.id, r.text
        FROM raw_snippets r
        LEFT JOIN snippet_quality sq ON sq.snippet_id = r.id
        WHERE sq.snippet_id IS NULL OR sq.lang IS NULL
        LIMIT :lim
        """
    )
    total = 0
    while True:
        with engine().begin() as conn:
            rows = list(conn.execute(q_pending, {"lim": batch}))
        if not rows:
            break
        to_insert = []
        for row in tqdm(rows, desc="lang", leave=False):
            lang = _detect_lang(row.text or "")
            norm = _normalize(row.text or "")
            to_insert.append({"sid": row.id, "lang": lang, "norm": norm})
        with engine().begin() as conn:
            conn.execute(text(
                "INSERT INTO snippet_quality (snippet_id, lang, text_normalized_v2, updated_at) "
                "VALUES (:sid, :lang, :norm, NOW()) "
                "ON CONFLICT (snippet_id) DO UPDATE "
                "SET lang=EXCLUDED.lang, text_normalized_v2=EXCLUDED.text_normalized_v2, updated_at=NOW()"
            ), to_insert)
        total += len(rows)
        log.info("filter_language: processed %d (running %d)", len(rows), total)
        if len(rows) < batch:
            break
    log.info("filter_language: DONE total=%d", total)
    return total


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", type=int, default=2000)
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args()
    logging.basicConfig(level=args.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    run(args.batch)
