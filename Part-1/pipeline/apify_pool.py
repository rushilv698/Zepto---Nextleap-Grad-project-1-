"""Round-robin pool over multiple Apify tokens.

Each token has ~$5 balance. When a token fails with a monetization / rate-limit
error we mark it dead for the process lifetime and rotate to the next. The pool
is thread-safe enough for our single-process orchestrator.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Callable

from apify_client import ApifyClient

from .settings import APIFY_TOKENS

log = logging.getLogger(__name__)

_DEAD_MARKERS = ("monthly-usage-hard-limit", "usage-hard-limit", "insufficient-credit", "payment-required")
# Errors that mean OUR request is bad (not the token) — do not rotate tokens.
_NON_ROTATABLE_MARKERS = ("input is not valid", "invalid-input", "field input", "invalid_request")


@dataclass
class _TokenState:
    token: str
    dead: bool = False
    calls: int = 0
    errors: int = 0


class ApifyPool:
    def __init__(self, tokens: list[str] | None = None) -> None:
        toks = tokens or APIFY_TOKENS
        if not toks:
            raise RuntimeError("No APIFY_TOKENS configured")
        self._states: list[_TokenState] = [_TokenState(t) for t in toks]
        self._idx = 0
        self._lock = threading.Lock()

    def _next_alive(self) -> _TokenState:
        with self._lock:
            for _ in range(len(self._states)):
                st = self._states[self._idx]
                self._idx = (self._idx + 1) % len(self._states)
                if not st.dead:
                    return st
            raise RuntimeError("All Apify tokens are exhausted")

    def run_actor(
        self,
        actor_id: str,
        run_input: dict[str, Any],
        *,
        max_token_switches: int = 6,
    ) -> list[dict[str, Any]]:
        """Run an Apify actor, rotating tokens on monetization/quota errors.

        Returns the full dataset as a list of dicts.
        """
        last_exc: Exception | None = None
        for attempt in range(max_token_switches):
            st = self._next_alive()
            client = ApifyClient(st.token)
            st.calls += 1
            try:
                run = client.actor(actor_id).call(run_input=run_input)
                if run is None:
                    raise RuntimeError("actor.call() returned None")
                # apify-client >=3 returns a Pydantic model with snake_case attrs.
                dataset_id = getattr(run, "default_dataset_id", None) or (run.get("defaultDatasetId") if isinstance(run, dict) else None)
                if not dataset_id:
                    raise RuntimeError(f"no default_dataset_id on run: {run!r}")
                items = list(client.dataset(dataset_id).iterate_items())
                log.info("apify actor=%s token=…%s items=%d", actor_id, st.token[-6:], len(items))
                return items
            except Exception as e:  # apify raises ApifyApiError; be permissive
                msg = str(e).lower()
                st.errors += 1
                if any(marker in msg for marker in _NON_ROTATABLE_MARKERS):
                    # our request is malformed — no point trying other tokens
                    log.error("apify actor=%s bad request (not rotating): %s", actor_id, msg[:200])
                    raise
                if any(marker in msg for marker in _DEAD_MARKERS):
                    log.warning("apify token …%s exhausted (%s), rotating", st.token[-6:], msg[:120])
                    st.dead = True
                else:
                    log.error("apify actor=%s failed on token …%s: %s", actor_id, st.token[-6:], msg[:200])
                last_exc = e
        raise RuntimeError(f"Apify actor {actor_id} failed after {max_token_switches} token switches") from last_exc

    def status(self) -> list[dict[str, Any]]:
        return [
            {"token_tail": s.token[-6:], "dead": s.dead, "calls": s.calls, "errors": s.errors}
            for s in self._states
        ]


_pool_singleton: ApifyPool | None = None


def get_pool() -> ApifyPool:
    global _pool_singleton
    if _pool_singleton is None:
        _pool_singleton = ApifyPool()
    return _pool_singleton
