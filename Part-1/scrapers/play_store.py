"""Google Play Store reviews via the google-play-scraper library.

We tried the Apify actor (KBD93wWVGA0u1JnMz) but it returns 0 reviews for the
Zepto app (Google Play's server-side rendering + free-tier daily caps). The
direct library hits Google's internal review API and is fast + free + unlimited.

Usage:
    python -m scrapers.play_store --max-reviews 5000
"""
from __future__ import annotations

import argparse
import logging

from google_play_scraper import Sort, reviews

from pipeline.settings import SOURCES
from pipeline.storage import Snippet, upsert_snippets

log = logging.getLogger(__name__)


def run(
    max_reviews: int = 5000,
    country: str = "in",
    lang: str = "en",
    app_id: str | None = None,
    brand: str = "zepto",
) -> int:
    app_id = app_id or SOURCES["app_ids"]["play_store"]
    app_url = f"https://play.google.com/store/apps/details?id={app_id}"

    all_snippets: list[Snippet] = []
    fetched = 0
    token = None
    while fetched < max_reviews:
        batch, token = reviews(
            app_id,
            lang=lang,
            country=country,
            sort=Sort.NEWEST,
            count=min(200, max_reviews - fetched),
            continuation_token=token,
        )
        if not batch:
            break
        for r in batch:
            text = (r.get("content") or "").strip()
            if not text:
                continue
            all_snippets.append(
                Snippet(
                    source="play_store",
                    brand=brand,
                    text=text,
                    source_url=app_url,
                    author=r.get("userName"),
                    posted_at=r.get("at"),
                    rating=r.get("score"),
                    lang=lang,
                    raw_metadata={
                        "reviewId": r.get("reviewId"),
                        "thumbsUp": r.get("thumbsUpCount"),
                        "reviewCreatedVersion": r.get("reviewCreatedVersion"),
                        "replyContent": r.get("replyContent"),
                    },
                )
            )
        fetched += len(batch)
        log.info("play_store: fetched %d / %d", fetched, max_reviews)
        if not token:
            break

    n = upsert_snippets(all_snippets)
    log.info("play_store[%s]: total fetched=%d saved=%d", brand, fetched, n)
    return n


def run_competitors(per_app: int = 2000, country: str = "in", lang: str = "en") -> int:
    total = 0
    for cfg in SOURCES.get("competitor_play_store_ids", []):
        try:
            total += run(
                max_reviews=per_app,
                country=country,
                lang=lang,
                app_id=cfg["id"],
                brand=cfg["brand"],
            )
        except Exception as e:
            log.exception("competitor %s failed: %s", cfg.get("brand"), e)
    return total


def _cli() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-reviews", type=int, default=5000)
    ap.add_argument("--country", default="in")
    ap.add_argument("--lang", default="en")
    ap.add_argument("--competitors", action="store_true", help="Scrape competitor apps instead of Zepto")
    ap.add_argument("--per-app", type=int, default=2000, help="Reviews per competitor app")
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args()
    logging.basicConfig(level=args.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.competitors:
        run_competitors(per_app=args.per_app, country=args.country, lang=args.lang)
    else:
        run(max_reviews=args.max_reviews, country=args.country, lang=args.lang)


if __name__ == "__main__":
    _cli()
