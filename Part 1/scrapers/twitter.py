"""Twitter/X scraper via Apify.

Primary actor: 61RPP7dywgiy0JPD0 (full-featured). Fallback: nfp1fpt5gUlBwPcor
(simpler search-only). We try primary; on failure the pool rotates tokens; if
the actor itself dies we fall back to the simpler one.

Usage:
    python -m scrapers.twitter --max-items 500
"""
from __future__ import annotations

import argparse
import logging
from datetime import datetime, timezone
from typing import Any

from pipeline.apify_pool import get_pool
from pipeline.settings import APIFY_TWITTER_ACTOR, APIFY_X_ACTOR, SOURCES
from pipeline.storage import Snippet, upsert_snippets

log = logging.getLogger(__name__)


def _parse_iso(v: Any) -> datetime | None:
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except Exception:
        return None


def _item_to_snippet(item: dict[str, Any]) -> Snippet | None:
    text = item.get("text") or item.get("fullText") or item.get("content")
    if not text or not isinstance(text, str):
        return None
    url = item.get("url") or item.get("tweetUrl") or item.get("twitterUrl")
    author_obj = item.get("author") or {}
    author = (
        item.get("username")
        or (author_obj.get("userName") if isinstance(author_obj, dict) else None)
        or (author_obj.get("screen_name") if isinstance(author_obj, dict) else None)
    )
    posted = _parse_iso(item.get("createdAt") or item.get("created_at") or item.get("date"))
    meta = {k: item.get(k) for k in ("likeCount", "retweetCount", "replyCount", "viewCount", "lang", "isReply") if k in item}
    return Snippet(
        source="twitter",
        text=text.strip(),
        source_url=url,
        author=author,
        posted_at=posted,
        lang=item.get("lang"),
        raw_metadata=meta,
    )


def run(max_items: int) -> int:
    pool = get_pool()
    cfg = SOURCES["twitter"]

    primary_input: dict[str, Any] = {
        "searchTerms": cfg["search_terms"],
        "maxItems": max_items,
        "sort": "Latest",
        "tweetLanguage": "en",
        "includeSearchTerms": True,
    }
    if cfg.get("handles"):
        primary_input["twitterHandles"] = cfg["handles"]

    try:
        items = pool.run_actor(APIFY_TWITTER_ACTOR, primary_input)
    except Exception as e:
        log.warning("primary twitter actor failed (%s), falling back", str(e)[:120])
        fallback_input = {
            "searchTerms": cfg["search_terms"],
            "sort": "Latest",
            "maxItems": max_items,
        }
        items = pool.run_actor(APIFY_X_ACTOR, fallback_input)

    snippets = [s for s in (_item_to_snippet(it) for it in items) if s is not None]
    n = upsert_snippets(snippets)
    log.info("twitter: fetched=%d saved=%d", len(items), n)
    return n


def _cli() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-items", type=int, default=500)
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args()
    logging.basicConfig(level=args.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    run(args.max_items)


if __name__ == "__main__":
    _cli()
