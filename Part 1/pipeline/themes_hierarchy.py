"""Phase C step 5 — Organize themes into a parent-child hierarchy.

After enough themes exist (seed + promoted), GPT-4.1 groups them into 3-6
parent buckets. This gives the dashboard a stable tree structure while allowing
detailed sub-themes to capture nuance.

Enforces max depth = 2 (parent → child, no grandchildren).

Usage:
    python -m pipeline.themes_hierarchy
"""
from __future__ import annotations

import argparse
import json
import logging

from sqlalchemy import text

from .openai_client import SYNTHESIZE_MODEL, chat_json
from .storage import engine

log = logging.getLogger(__name__)

TAXONOMY_VERSION = 1


_PROMPT = """You are structuring a flat list of user-behavior themes for Zepto's
Growth team into a 2-level hierarchy: 3-6 broad PARENT themes, each covering
2-6 of the input themes as CHILDREN. Every input theme must appear as either
a parent or a child. No orphans.

INPUT THEMES:
{themes_block}

Return json only:
{
  "parents": [
    {
      "name": "<2-6 word parent name — should feel like a category>",
      "definition": "<one sentence>",
      "children_ids": [<input theme ids to nest under this parent>]
    }
    // 3-6 parents total
  ]
}

Rules:
- Every input theme id must appear in exactly one children_ids list.
- Parents must be broader than children — think 'Search & Discovery' > 'Category Search Frustration'.
- Do NOT duplicate an existing theme name as a parent.
- Valid json only.
"""


def run(model: str = SYNTHESIZE_MODEL) -> int:
    with engine().begin() as conn:
        # Pull only leaf themes (no parent yet, not merged/archived, current version)
        rows = list(conn.execute(text(
            "SELECT id, name, definition FROM themes "
            "WHERE taxonomy_version = :v AND parent_id IS NULL "
            "AND status IN ('seed', 'promoted') "
            "AND merged_into IS NULL"
        ), {"v": TAXONOMY_VERSION}).all())

    if len(rows) < 3:
        log.info("hierarchy: only %d themes; nothing to organize", len(rows))
        return 0

    themes_block = "\n".join(f"- [id={r.id}] {r.name}: {r.definition}" for r in rows)
    prompt = _PROMPT.replace("{themes_block}", themes_block)
    raw = chat_json(prompt, model=model, temperature=0.3)
    parents = raw.get("parents") or []
    if not parents:
        log.error("hierarchy: model returned no parents; raw=%s", str(raw)[:400])
        return 0

    all_ids = {r.id for r in rows}
    covered = set()
    with engine().begin() as conn:
        for p in parents:
            name = (p.get("name") or "").strip()
            defn = (p.get("definition") or "").strip()
            children = [int(cid) for cid in (p.get("children_ids") or []) if isinstance(cid, int) and int(cid) in all_ids]
            if not name or not children:
                continue
            parent_id = conn.execute(text(
                "INSERT INTO themes (name, definition, taxonomy_version, status) "
                "VALUES (:name, :defn, :v, 'seed') RETURNING id"
            ), {"name": name[:120], "defn": defn[:500], "v": TAXONOMY_VERSION}).scalar_one()
            for cid in children:
                conn.execute(text(
                    "UPDATE themes SET parent_id = :pid WHERE id = :cid"
                ), {"pid": parent_id, "cid": cid})
                covered.add(cid)
            log.info("hierarchy: parent '%s' (#%d) → %d children", name, parent_id, len(children))

    missing = all_ids - covered
    if missing:
        log.warning("hierarchy: %d themes uncovered by LLM: %s", len(missing), list(missing)[:10])
    return len(parents)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=SYNTHESIZE_MODEL)
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args()
    logging.basicConfig(level=args.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    run(args.model)
