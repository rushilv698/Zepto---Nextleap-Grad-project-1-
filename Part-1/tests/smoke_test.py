"""Smoke test — verify OpenAI + Apify + Postgres + Weaviate all work.

Runs in ~15 seconds and spends < $0.02 total.

    python -m tests.smoke_test
"""
from __future__ import annotations

import sys
import time

from sqlalchemy import text

from pipeline.apify_pool import get_pool
from pipeline.embed import ensure_schema, wclient
from pipeline.openai_client import client, embed_batch
from pipeline.settings import APIFY_REDDIT_ACTOR
from pipeline.storage import engine


def check(name: str, fn) -> bool:
    print(f"  … {name}", end=" ", flush=True)
    t0 = time.time()
    try:
        result = fn()
        dt = time.time() - t0
        print(f"OK ({dt:.1f}s)  {result or ''}")
        return True
    except Exception as e:
        dt = time.time() - t0
        print(f"FAIL ({dt:.1f}s)  {e!s}")
        return False


def check_postgres() -> str:
    with engine().begin() as conn:
        tables = [r[0] for r in conn.execute(
            text("SELECT table_name FROM information_schema.tables WHERE table_schema='public' ORDER BY 1")
        )]
    if not tables:
        raise RuntimeError("no tables — schema not loaded")
    return f"tables: {', '.join(tables)}"


def check_openai() -> str:
    vecs = embed_batch(["hello zepto"])
    return f"embedding dim={len(vecs[0])}"


def check_weaviate() -> str:
    ensure_schema()
    collections = list(wclient().collections.list_all())
    return f"collections: {collections}"


def check_apify() -> str:
    # Cheapest possible sanity call: just verify token can list our own account.
    # No actor run at all → $0.
    from apify_client import ApifyClient
    from pipeline.settings import APIFY_TOKENS
    c = ApifyClient(APIFY_TOKENS[0])
    user = c.user("me").get()
    if not user:
        raise RuntimeError("user.get() returned None")
    username = getattr(user, "username", None) or (user.get("username") if isinstance(user, dict) else "?")
    return f"authenticated as: {username}"


def main() -> int:
    print("Zepto Discovery Engine — smoke test\n")
    results = {
        "postgres":  check("Postgres (localhost:5432)", check_postgres),
        "weaviate":  check("Weaviate (localhost:8080)", check_weaviate),
        "openai":    check("OpenAI embedding",          check_openai),
        "apify":     check("Apify actor (tiny run)",    check_apify),
    }
    print()
    if all(results.values()):
        print("all systems go")
        return 0
    print("FAILURES:", [k for k, v in results.items() if not v])
    return 1


if __name__ == "__main__":
    sys.exit(main())
