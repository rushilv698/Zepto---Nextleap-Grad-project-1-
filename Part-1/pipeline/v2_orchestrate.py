"""End-to-end v2 pipeline orchestrator.

Runs Phase B → Phase C → Phase D in the correct order with sane defaults.
Each stage is idempotent (re-running doesn't duplicate work).

Usage:
    python -m pipeline.v2_orchestrate                  # full run
    python -m pipeline.v2_orchestrate --stage themes   # from themes onward
    python -m pipeline.v2_orchestrate --skip-embed     # if already embedded
"""
from __future__ import annotations

import argparse
import logging
import time

from . import (
    dedupe,
    embed,
    filter_language,
    filter_llm,
    info_value,
    insights_generate,
    themes_assign,
    themes_consolidate,
    themes_hierarchy,
    themes_promote,
    themes_seed,
    validate,
)

log = logging.getLogger(__name__)

STAGES = ("language", "llm", "embed", "dedupe", "info_value",
          "seed", "assign", "promote", "hierarchy", "consolidate",
          "insights", "validate")


def _stage(name: str, fn, **kwargs):
    log.info("=" * 60)
    log.info("STAGE: %s", name.upper())
    log.info("=" * 60)
    t0 = time.time()
    try:
        result = fn(**kwargs)
    except Exception as e:
        log.exception("stage %s failed: %s", name, e)
        return None
    log.info("stage %s done in %.1fs (result=%s)", name, time.time() - t0, result)
    return result


def run(from_stage: str = "language", skip_embed: bool = False,
        filter_workers: int = 12, filter_limit: int = 20000,
        assign_threshold: float = 0.75, promote_min: int = 8) -> None:
    idx = STAGES.index(from_stage) if from_stage in STAGES else 0
    stages_to_run = STAGES[idx:]

    for stage in stages_to_run:
        if stage == "language":
            _stage("language", filter_language.run, batch=2000)
        elif stage == "llm":
            _stage("filter_llm", filter_llm.run, limit=filter_limit, workers=filter_workers)
        elif stage == "embed":
            if skip_embed:
                log.info("skipping embed stage (--skip-embed)")
                continue
            _stage("embed", embed.embed_and_index, batch_size=100, max_batches=200)
        elif stage == "dedupe":
            _stage("dedupe", dedupe.run, threshold=0.95, batch_size=500, max_neighbours=5)
        elif stage == "info_value":
            _stage("info_value", info_value.run, batch=5000)
        elif stage == "seed":
            _stage("themes_seed", themes_seed.run, n=200)
        elif stage == "assign":
            _stage("themes_assign", themes_assign.run, threshold=assign_threshold, batch=500, max_batches=40)
        elif stage == "promote":
            _stage("themes_promote", themes_promote.run, min_members=promote_min)
        elif stage == "hierarchy":
            _stage("themes_hierarchy", themes_hierarchy.run)
        elif stage == "consolidate":
            _stage("themes_consolidate", themes_consolidate.run, threshold=0.88)
        elif stage == "insights":
            _stage("insights_generate", insights_generate.run, min_members=promote_min)
        elif stage == "validate":
            _stage("validate", validate.run)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", choices=list(STAGES), default="language",
                    help="Start from this stage")
    ap.add_argument("--skip-embed", action="store_true")
    ap.add_argument("--filter-workers", type=int, default=12)
    ap.add_argument("--filter-limit", type=int, default=20000)
    ap.add_argument("--assign-threshold", type=float, default=0.75)
    ap.add_argument("--promote-min", type=int, default=8)
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args()
    logging.basicConfig(level=args.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    run(from_stage=args.stage, skip_embed=args.skip_embed,
        filter_workers=args.filter_workers, filter_limit=args.filter_limit,
        assign_threshold=args.assign_threshold, promote_min=args.promote_min)


if __name__ == "__main__":
    main()
