"""YouTube comments — free via youtube-comment-downloader (no API key).

Pulls comments from a curated list of Zepto / quick-commerce videos configured
in config/sources.yaml → `youtube_videos`. These are where users write about
category exploration mental models organically.

Usage:
    python -m scrapers.youtube --per-video 300
"""
from __future__ import annotations

import argparse
import logging
from datetime import datetime, timezone
from typing import Any

from youtube_comment_downloader import SORT_BY_POPULAR, YoutubeCommentDownloader

from pipeline.settings import SOURCES
from pipeline.storage import Snippet, upsert_snippets

log = logging.getLogger(__name__)


def _parse_ts(v: Any) -> datetime | None:
    if not v:
        return None
    # youtube-comment-downloader returns strings like "2 years ago" (relative)
    # AND unix seconds in "time_parsed". Prefer time_parsed.
    if isinstance(v, (int, float)):
        try:
            return datetime.fromtimestamp(float(v), tz=timezone.utc)
        except Exception:
            return None
    return None


def _to_snippet(c: dict[str, Any], video_id: str) -> Snippet | None:
    text = (c.get("text") or "").strip()
    if not text or len(text) < 8:
        return None
    return Snippet(
        source="youtube",
        text=text,
        source_url=f"https://www.youtube.com/watch?v={video_id}",
        author=c.get("author"),
        posted_at=_parse_ts(c.get("time_parsed")),
        lang=None,
        raw_metadata={
            "video_id": video_id,
            "cid": c.get("cid"),
            "votes": c.get("votes"),
            "replies": c.get("replies"),
            "heart": c.get("heart"),
            "time_text": c.get("time"),
        },
    )


def run(per_video: int = 300) -> int:
    videos = SOURCES.get("youtube_videos") or []
    if not videos:
        log.warning("youtube: no videos configured")
        return 0

    downloader = YoutubeCommentDownloader()
    all_snippets: list[Snippet] = []

    for v in videos:
        video_id = v["id"] if isinstance(v, dict) else str(v)
        try:
            gen = downloader.get_comments(video_id, sort_by=SORT_BY_POPULAR)
            fetched_this_video = 0
            for c in gen:
                snip = _to_snippet(c, video_id)
                if snip:
                    all_snippets.append(snip)
                fetched_this_video += 1
                if fetched_this_video >= per_video:
                    break
            log.info("youtube[%s]: pulled %d comments", video_id, fetched_this_video)
        except Exception as e:
            log.warning("youtube[%s] failed: %s", video_id, str(e)[:200])

    n = upsert_snippets(all_snippets)
    log.info("youtube: total_pulled=%d saved=%d", len(all_snippets), n)
    return n


def _cli() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-video", type=int, default=300)
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args()
    logging.basicConfig(level=args.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    run(args.per_video)


if __name__ == "__main__":
    _cli()
