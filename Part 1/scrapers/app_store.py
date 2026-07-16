"""Apple App Store reviews via Apify actor 4qRgh5vXXsv0bKa1l.

STATUS: DISABLED. The chosen actor requires Apify Paid Plan (won't run on our
credit balance). The direct `app-store-scraper` library is also broken (Apple
gated the endpoint). RSS feed also empty for India. Skipping App Store for MVP;
Play Store gives us the majority of India user feedback anyway.


Usage:
    python -m scrapers.app_store --max-items 1000
"""
from __future__ import annotations

import argparse
import logging
from datetime import datetime, timezone
from typing import Any

from pipeline.apify_pool import get_pool
from pipeline.settings import APIFY_APPSTORE_ACTOR, SOURCES
from pipeline.storage import Snippet, upsert_snippets

log = logging.getLogger(__name__)


def _parse_iso(v: Any) -> datetime | None:
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except Exception:
        return None


def _item_to_snippet(item: dict[str, Any], app_url: str) -> Snippet | None:
    title = (item.get("title") or "").strip()
    body = (item.get("review") or item.get("text") or item.get("content") or "").strip()
    text = " — ".join(p for p in (title, body) if p)
    if not text:
        return None
    return Snippet(
        source="app_store",
        text=text,
        source_url=item.get("url") or app_url,
        author=item.get("userName") or item.get("author"),
        posted_at=_parse_iso(item.get("date") or item.get("createdAt")),
        lang="en",
        rating=item.get("rating") or item.get("score"),
        raw_metadata={
            k: item.get(k)
            for k in ("reviewId", "isEdited", "developerResponse", "country", "appVersion")
            if k in item
        },
    )


def run(max_items: int = 1000, countries: list[str] | None = None) -> int:
    pool = get_pool()
    cfg = SOURCES["app_ids"]["app_store"]
    app_id = str(cfg["id"])
    country_list = countries or ["in"]
    start_urls = [f"https://apps.apple.com/{c}/app/id{app_id}" for c in country_list]
    app_url = start_urls[0]

    run_input = {
        "appIds": [app_id],
        "country": country_list[0],
        "startUrls": start_urls,
        "maxItems": max_items,
    }

    items = pool.run_actor(APIFY_APPSTORE_ACTOR, run_input)
    snippets = [s for s in (_item_to_snippet(it, app_url) for it in items) if s is not None]
    n = upsert_snippets(snippets)
    log.info("app_store: fetched=%d saved=%d", len(items), n)
    return n


def _cli() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-items", type=int, default=1000)
    ap.add_argument("--countries", nargs="*", default=["in"])
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args()
    logging.basicConfig(level=args.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    run(max_items=args.max_items, countries=args.countries)


if __name__ == "__main__":
    _cli()
