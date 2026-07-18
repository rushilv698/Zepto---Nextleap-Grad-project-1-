"""Tier-1 keyword pre-filter over raw_snippets.

Marks snippets that mention any relevance keyword AND either mention Zepto or a
quick-commerce competitor, so we don't waste LLM budget on unrelated Reddit
noise.

We keep the raw table untouched and write into a lightweight `filtered_snippets`
view materialized as a table for speed. Call `refresh()` after every scrape.
"""
from __future__ import annotations

import logging
import re

from sqlalchemy import text

from .settings import SOURCES
from .storage import engine

log = logging.getLogger(__name__)

_BRAND_KEYWORDS = [
    "zepto", "blinkit", "instamart", "swiggy instamart", "bigbasket", "grofers",
    "quick commerce", "10 minute", "10-minute", "10 min",
]

_ENSURE_TABLE = text(
    """
    CREATE TABLE IF NOT EXISTS filtered_snippets (
        snippet_id      TEXT PRIMARY KEY REFERENCES raw_snippets(id) ON DELETE CASCADE,
        matched_keywords TEXT[] NOT NULL DEFAULT '{}',
        filtered_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    """
)


def _make_regex(terms: list[str]) -> re.Pattern:
    joined = "|".join(re.escape(t.lower()) for t in terms)
    return re.compile(rf"\b({joined})", re.IGNORECASE)


def refresh(batch: int = 5000) -> int:
    """Scan raw_snippets not yet in filtered_snippets; keyword-filter; insert matches."""
    with engine().begin() as conn:
        conn.execute(_ENSURE_TABLE)

    kw_re = _make_regex(SOURCES["relevance_keywords"])
    brand_re = _make_regex(_BRAND_KEYWORDS)

    q = text(
        """
        SELECT r.id, r.text_normalized, r.source
        FROM raw_snippets r
        LEFT JOIN filtered_snippets f ON f.snippet_id = r.id
        WHERE f.snippet_id IS NULL
        ORDER BY r.ingested_at
        LIMIT :lim
        """
    )
    insert_sql = text(
        "INSERT INTO filtered_snippets (snippet_id, matched_keywords) VALUES (:snippet_id, :kw) "
        "ON CONFLICT (snippet_id) DO NOTHING"
    )

    total = 0
    while True:
        with engine().begin() as conn:
            rows = list(conn.execute(q, {"lim": batch}))
            if not rows:
                break
            to_insert = []
            for rid, norm, source in rows:
                if not norm:
                    continue
                # App-store reviews ARE Zepto feedback by definition — pass all
                # through to LLM classification (still cheap at ~$0.001/snippet).
                if source in {"play_store", "app_store"}:
                    to_insert.append({"snippet_id": rid, "kw": ["_app_review"]})
                    continue
                # Social sources: require both brand mention AND relevance keyword.
                if not brand_re.search(norm):
                    continue
                kw_matches = list({m.group(1).lower() for m in kw_re.finditer(norm)})
                if not kw_matches:
                    continue
                to_insert.append({"snippet_id": rid, "kw": kw_matches})
            if to_insert:
                conn.execute(insert_sql, to_insert)
                total += len(to_insert)
        if len(rows) < batch:
            break
    log.info("filter: matched %d new snippets", total)
    return total


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args()
    logging.basicConfig(level=args.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    refresh()
