"""End-to-end pipeline runner.

Usage:
    python -m pipeline.orchestrate --once                 # run every stage once
    python -m pipeline.orchestrate --schedule             # daemon on cron schedule
    python -m pipeline.orchestrate --stage extract        # run a single stage
"""
from __future__ import annotations

import argparse
import logging
import time

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from pipeline import embed, extract, filter as filter_stage, synthesize
from scrapers import app_store, play_store, reddit, twitter

log = logging.getLogger(__name__)


def run_scrape(reddit_max: int = 500, twitter_max: int = 500, app_count: int = 1000) -> None:
    log.info("=== scrape ===")
    try:
        play_store.run(max_reviews=app_count)
    except Exception as e:
        log.exception("play_store failed: %s", e)
    try:
        app_store.run(max_items=min(1000, app_count))
    except Exception as e:
        log.exception("app_store failed: %s", e)
    try:
        reddit.run(mode="search", max_items=reddit_max)
    except Exception as e:
        log.exception("reddit search failed: %s", e)
    try:
        reddit.run(mode="community", max_items=reddit_max // 2)
    except Exception as e:
        log.exception("reddit community failed: %s", e)
    try:
        twitter.run(max_items=twitter_max)
    except Exception as e:
        log.exception("twitter failed: %s", e)


def run_process(extract_limit: int = 1000) -> None:
    log.info("=== filter ===")
    filter_stage.refresh()
    log.info("=== embed ===")
    embed.embed_and_index()
    log.info("=== extract ===")
    extract.run(limit=extract_limit)


def run_synth() -> None:
    log.info("=== synthesize ===")
    synthesize.run()


def run_once(**kw) -> None:
    run_scrape(**{k: v for k, v in kw.items() if k in {"reddit_max", "twitter_max", "app_count"}})
    run_process(extract_limit=kw.get("extract_limit", 1000))
    run_synth()


def run_schedule() -> None:
    sched = BlockingScheduler(timezone="Asia/Kolkata")
    sched.add_job(run_scrape,  CronTrigger(hour=2, minute=0),  id="scrape",  max_instances=1)
    sched.add_job(run_process, CronTrigger(hour=3, minute=30), id="process", max_instances=1)
    sched.add_job(run_synth,   CronTrigger(day_of_week="mon", hour=5, minute=0), id="synth", max_instances=1)
    log.info("scheduler starting; ctrl-c to exit")
    sched.start()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="Run full pipeline once and exit")
    ap.add_argument("--schedule", action="store_true", help="Run as daemon on cron schedule")
    ap.add_argument("--stage", choices=["scrape", "filter", "embed", "extract", "synth"], help="Run a single stage")
    ap.add_argument("--reddit-max", type=int, default=500)
    ap.add_argument("--twitter-max", type=int, default=500)
    ap.add_argument("--app-count", type=int, default=1000)
    ap.add_argument("--extract-limit", type=int, default=1000)
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args()

    logging.basicConfig(level=args.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if args.stage == "scrape":
        run_scrape(reddit_max=args.reddit_max, twitter_max=args.twitter_max, app_count=args.app_count)
    elif args.stage == "filter":
        filter_stage.refresh()
    elif args.stage == "embed":
        embed.embed_and_index()
    elif args.stage == "extract":
        extract.run(limit=args.extract_limit)
    elif args.stage == "synth":
        synthesize.run()
    elif args.schedule:
        run_schedule()
    elif args.once:
        run_once(
            reddit_max=args.reddit_max,
            twitter_max=args.twitter_max,
            app_count=args.app_count,
            extract_limit=args.extract_limit,
        )
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
