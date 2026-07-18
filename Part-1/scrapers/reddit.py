"""Reddit scraper via Apify actor oAuCIx3ItNrs2okjQ.

Two modes:
  1. Community pull   — pull latest posts + comments from configured subreddits.
  2. Search pull      — search Reddit for Zepto-related terms across all of Reddit.

Usage:
    python -m scrapers.reddit --mode community --max-items 200
    python -m scrapers.reddit --mode search --max-items 500
"""
from __future__ import annotations

import argparse
import logging
from datetime import datetime, timezone
from typing import Any

from pipeline.apify_pool import get_pool
from pipeline.settings import APIFY_REDDIT_ACTOR, SOURCES
from pipeline.storage import Snippet, upsert_snippets

log = logging.getLogger(__name__)


def _parse_iso(v: Any) -> datetime | None:
    if not v:
        return None
    if isinstance(v, (int, float)):
        try:
            return datetime.fromtimestamp(float(v), tz=timezone.utc)
        except Exception:
            return None
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except Exception:
        return None


def _item_to_snippet(item: dict[str, Any]) -> Snippet | None:
    """The Apify Reddit actor returns heterogeneous items (posts, comments, users).
    We pick text-bearing ones and normalize to Snippet."""
    # posts: title + body; comments: body
    text_parts: list[str] = []
    for k in ("title", "body", "text", "selftext", "comment"):
        v = item.get(k)
        if isinstance(v, str) and v.strip():
            text_parts.append(v.strip())
    text = " — ".join(dict.fromkeys(text_parts))  # dedupe while preserving order
    if not text:
        return None

    url = item.get("url") or item.get("commentUrl") or item.get("postUrl")
    author = item.get("username") or item.get("author") or item.get("user")
    posted = _parse_iso(
        item.get("createdAt") or item.get("created_utc") or item.get("date") or item.get("timestamp")
    )
    return Snippet(
        source="reddit",
        text=text,
        source_url=url,
        author=author,
        posted_at=posted,
        raw_metadata={k: item.get(k) for k in ("subreddit", "postId", "commentId", "upVotes", "downVotes", "numberOfComments") if k in item},
    )


def run(mode: str, max_items: int) -> int:
    pool = get_pool()
    cfg = SOURCES["reddit"]

    if mode == "community":
        start_urls = [{"url": f"https://www.reddit.com/r/{sub}/"} for sub in cfg["subreddits"]]
        searches = None
    elif mode == "deep":
        start_urls = []
        searches = cfg.get("deep_search_terms") or cfg["search_terms"]
    else:  # search
        start_urls = []
        searches = cfg["search_terms"]

    run_input: dict[str, Any] = {
        "skipComments": False,
        "skipUserPosts": False,
        "skipCommunity": False,
        "includeMediaLinks": False,
        "ignoreStartUrls": False,
        "searchPosts": True,
        "searchComments": True,
        "searchCommunities": False,
        "searchUsers": False,
        "searchMedia": False,
        "sort": "new",
        "includeNSFW": True,
        "maxItems": max_items,
        "maxPostCount": max_items,
        "maxComments": max(50, max_items // 4),
        "maxCommunitiesCount": len(cfg["subreddits"]),
        "scrollTimeout": 40,
        "proxy": {"useApifyProxy": True, "apifyProxyGroups": ["RESIDENTIAL"]},
        "debugMode": False,
    }
    if start_urls:
        run_input["startUrls"] = start_urls
    if searches:
        run_input["searches"] = searches

    items = pool.run_actor(APIFY_REDDIT_ACTOR, run_input)
    snippets = [s for s in (_item_to_snippet(it) for it in items) if s is not None]
    n = upsert_snippets(snippets)
    log.info("reddit mode=%s: fetched=%d saved=%d", mode, len(items), n)
    return n


def _cli() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["community", "search", "deep"], default="search")
    ap.add_argument("--max-items", type=int, default=500)
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args()
    logging.basicConfig(level=args.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    run(args.mode, args.max_items)


if __name__ == "__main__":
    _cli()
