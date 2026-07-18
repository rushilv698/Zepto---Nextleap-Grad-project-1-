"""Category co-occurrence + gateway analysis.

Two questions answered:

1. **Co-occurrence**: which categories are mentioned TOGETHER in the same
   snippet? High co-occurrence = users mentally group them; low = discovery gap.
2. **Gateways**: which categories are mentioned as a `currently_buying →
   category_avoiding` pair most often? These are the transitions users want but
   don't make.
3. **Novelty-moment** analysis: for snippets where novelty_moment=true, what
   category triggered the discovery? These are the actual exploration success
   stories.

Results are written to a table `category_edges` for the dashboard to visualize.
"""
from __future__ import annotations

import logging
from collections import Counter
from itertools import combinations

from sqlalchemy import text

from .storage import engine

log = logging.getLogger(__name__)

_ENSURE_TABLE = text(
    """
    CREATE TABLE IF NOT EXISTS category_edges (
        src        TEXT NOT NULL,
        dst        TEXT NOT NULL,
        edge_type  TEXT NOT NULL,        -- 'co_mention' | 'gateway_want' | 'gateway_success'
        weight     INT  NOT NULL,
        computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (src, dst, edge_type)
    );
    """
)


def _co_mention() -> Counter:
    """From discovery_signals.categories_mentioned, count all unordered pairs
    of categories that appear in the same snippet (excluding 'unknown')."""
    q = text("SELECT categories_mentioned FROM discovery_signals WHERE exploration_signal!='none'")
    counts: Counter = Counter()
    with engine().begin() as conn:
        for (cats,) in conn.execute(q):
            cats = [c for c in (cats or []) if c and c != "unknown"]
            for a, b in combinations(sorted(set(cats)), 2):
                counts[(a, b)] += 1
    return counts


def _gateway_want() -> Counter:
    """From extracted_insights, count (currently_buying → avoiding) transitions
    users WANT (but signal they aren't making)."""
    q = text(
        """
        SELECT category_currently_buying, category_avoiding, COUNT(*)
        FROM extracted_insights
        WHERE intent IN ('Exploration_Blocker','Unmet_Need','Discovery_Request')
          AND category_currently_buying != 'unknown'
          AND category_avoiding         != 'unknown'
          AND category_currently_buying != category_avoiding
        GROUP BY 1,2
        """
    )
    with engine().begin() as conn:
        return Counter({(src, dst): n for src, dst, n in conn.execute(q)})


def _gateway_success() -> Counter:
    """Novelty moments from discovery_signals: user discovered a new category.
    We record (category_currently_buying [from extracted_insights] → discovered
    category) so we can trace which gateways ACTUALLY worked."""
    q = text(
        """
        SELECT ei.category_currently_buying AS src, unnest(d.categories_mentioned) AS dst, COUNT(*)
        FROM discovery_signals d
        JOIN extracted_insights ei ON ei.snippet_id = d.snippet_id
        WHERE d.novelty_moment = true
          AND ei.category_currently_buying != 'unknown'
        GROUP BY 1,2
        HAVING unnest(d.categories_mentioned) != 'unknown'
           AND ei.category_currently_buying != unnest(d.categories_mentioned)
        """
    )
    # HAVING with unnest is not portable; do the pair-building in Python instead.
    q2 = text(
        """
        SELECT ei.category_currently_buying AS src, d.categories_mentioned AS cats
        FROM discovery_signals d
        JOIN extracted_insights ei ON ei.snippet_id = d.snippet_id
        WHERE d.novelty_moment = true
          AND ei.category_currently_buying != 'unknown'
        """
    )
    counts: Counter = Counter()
    with engine().begin() as conn:
        for src, cats in conn.execute(q2):
            for dst in (cats or []):
                if dst and dst != "unknown" and dst != src:
                    counts[(src, dst)] += 1
    return counts


def run() -> dict:
    with engine().begin() as conn:
        conn.execute(_ENSURE_TABLE)
        conn.execute(text("DELETE FROM category_edges"))

    co  = _co_mention()
    gw  = _gateway_want()
    gs  = _gateway_success()

    rows = (
        [{"src": a, "dst": b, "edge_type": "co_mention", "weight": n} for (a, b), n in co.items()] +
        [{"src": a, "dst": b, "edge_type": "gateway_want", "weight": n} for (a, b), n in gw.items()] +
        [{"src": a, "dst": b, "edge_type": "gateway_success", "weight": n} for (a, b), n in gs.items()]
    )
    if rows:
        with engine().begin() as conn:
            conn.execute(
                text("INSERT INTO category_edges (src, dst, edge_type, weight) "
                     "VALUES (:src, :dst, :edge_type, :weight)"),
                rows,
            )

    summary = {
        "co_mention_edges":     len(co),
        "gateway_want_edges":   len(gw),
        "gateway_success_edges": len(gs),
        "top_co_mention":       co.most_common(5),
        "top_gateway_want":     gw.most_common(5),
        "top_gateway_success":  gs.most_common(5),
    }
    log.info("cooccurrence summary: %s", summary)
    return summary


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args()
    logging.basicConfig(level=args.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    from pprint import pprint
    pprint(run())
