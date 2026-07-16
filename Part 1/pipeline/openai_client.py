"""LLM client — supports OpenAI + DeepSeek (OpenAI-compatible API).

Route by model prefix:
  * `deepseek-...` → DeepSeek (bypasses OpenAI RPD quota)
  * `text-embedding-...` → OpenAI embeddings (DeepSeek has no embedding endpoint)
  * everything else → OpenAI

Embed always uses OpenAI (DeepSeek has no embedding endpoint).
"""
from __future__ import annotations

import json
import logging

from openai import OpenAI
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_random_exponential

from .settings import DEEPSEEK_API_KEY, OPENAI_API_KEY

log = logging.getLogger(__name__)

_openai_client: OpenAI | None = None
_deepseek_client: OpenAI | None = None

EMBED_MODEL = "text-embedding-3-small"
# High-volume extraction — DeepSeek is ~$0.14/1M input, no RPD cap for us
EXTRACT_MODEL = "deepseek-chat"
# Premium synthesis — GPT-4.1 for best instruction-following on constrained outputs
SYNTHESIZE_MODEL = "gpt-4.1"


def _openai() -> OpenAI:
    global _openai_client
    if _openai_client is None:
        _openai_client = OpenAI(api_key=OPENAI_API_KEY)
    return _openai_client


def _deepseek() -> OpenAI:
    global _deepseek_client
    if _deepseek_client is None:
        if not DEEPSEEK_API_KEY:
            raise RuntimeError("DEEPSEEK_API_KEY not set")
        _deepseek_client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")
    return _deepseek_client


def _client_for(model: str) -> OpenAI:
    return _deepseek() if model.startswith("deepseek") else _openai()


# Kept for callers that want raw client access
def client() -> OpenAI:
    return _openai()


@retry(
    reraise=True,
    stop=stop_after_attempt(4),
    wait=wait_random_exponential(min=1, max=20),
    retry=retry_if_exception_type(Exception),
)
def embed_batch(texts: list[str]) -> list[list[float]]:
    resp = _openai().embeddings.create(model=EMBED_MODEL, input=texts)
    return [d.embedding for d in resp.data]


@retry(
    reraise=True,
    stop=stop_after_attempt(4),
    wait=wait_random_exponential(min=1, max=20),
    retry=retry_if_exception_type(Exception),
)
def chat_json(prompt: str, *, model: str = EXTRACT_MODEL, temperature: float = 0.1) -> dict:
    c = _client_for(model)
    resp = c.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=temperature,
        response_format={"type": "json_object"},
    )
    content = resp.choices[0].message.content or "{}"
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        log.warning("model returned non-JSON: %s", content[:200])
        return {}
