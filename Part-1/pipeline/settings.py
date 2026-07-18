"""Central config loader — reads .env + config/*.yaml once, exposes typed constants."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# Empty defaults so the dashboard can import this module without .env
# (Streamlit Cloud demo mode). The scraper/pipeline modules that need these
# will fail at their own point of use with a clearer error.
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
APIFY_TOKENS: list[str] = [t.strip() for t in os.environ.get("APIFY_TOKENS", "").split(",") if t.strip()]

APIFY_REDDIT_ACTOR = os.environ.get("APIFY_REDDIT_ACTOR", "oAuCIx3ItNrs2okjQ")
APIFY_TWITTER_ACTOR = os.environ.get("APIFY_TWITTER_ACTOR", "61RPP7dywgiy0JPD0")
APIFY_X_ACTOR = os.environ.get("APIFY_X_ACTOR", "nfp1fpt5gUlBwPcor")
APIFY_PLAYSTORE_ACTOR = os.environ.get("APIFY_PLAYSTORE_ACTOR", "KBD93wWVGA0u1JnMz")
APIFY_APPSTORE_ACTOR = os.environ.get("APIFY_APPSTORE_ACTOR", "4qRgh5vXXsv0bKa1l")

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql+psycopg2://zepto:zepto@localhost:5432/zepto_discovery")
WEAVIATE_URL = os.environ.get("WEAVIATE_URL", "http://localhost:8080")

DATA_LAKE_DIR = Path(os.environ.get("DATA_LAKE_DIR", ROOT / "data_lake")).resolve()
DATA_LAKE_DIR.mkdir(parents=True, exist_ok=True)

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")

_SOURCES_PATH = ROOT / "config" / "sources.yaml"
_TAXONOMY_PATH = ROOT / "config" / "taxonomy.yaml"
_PROMPTS_DIR = ROOT / "config" / "prompts"


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open() as f:
        return yaml.safe_load(f)


SOURCES: dict[str, Any] = load_yaml(_SOURCES_PATH)
TAXONOMY: dict[str, Any] = load_yaml(_TAXONOMY_PATH)


def load_prompt(name: str) -> str:
    return (_PROMPTS_DIR / name).read_text()
