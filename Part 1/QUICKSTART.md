# QUICKSTART — bring the Zepto Discovery Engine online

## Prerequisites you must install

1. **Docker Desktop** (for Mac): https://www.docker.com/products/docker-desktop/
   — needed for local Postgres + Weaviate.
2. **Python 3.11 or 3.12** (recommended; 3.13/3.14 may lack wheels for some pinned deps).
   `brew install python@3.12` if needed.

## First-time setup (10 minutes)

```bash
cd "Part 1"

# 1. Python virtual env
python3.12 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt

# 2. Start Postgres + Weaviate
docker compose up -d
# wait ~10s, then verify:
docker compose ps           # both should be "Up"
psql postgresql://zepto:zepto@localhost:5432/zepto_discovery -c "\dt"

# 3. Sanity-check config loads
python -c "from pipeline.settings import OPENAI_API_KEY, APIFY_TOKENS; print('ok', len(APIFY_TOKENS), 'apify tokens')"
```

## Run the pipeline (in order)

```bash
# --- SCRAPE (fills raw_snippets + data_lake/*.parquet) — all via Apify ---
python -m scrapers.play_store --max-reviews 5000              # Apify
python -m scrapers.app_store  --max-items   1000              # Apify
python -m scrapers.reddit --mode search    --max-items 500    # ~$2 of Apify budget
python -m scrapers.reddit --mode community --max-items 300
python -m scrapers.twitter --max-items 500                    # ~$0.20 of Apify budget

# --- PROCESS ---
python -m pipeline.filter                        # keyword pre-filter
python -m pipeline.embed                         # OpenAI embeddings → Weaviate
python -m pipeline.extract --limit 500           # GPT-4o-mini structured extraction

# --- SYNTHESIZE (weekly) ---
python -m pipeline.synthesize                    # DBSCAN clusters + GPT-4o insight cards

# --- DASHBOARD ---
streamlit run dashboard/app.py                   # http://localhost:8501
```

## Or run everything end-to-end

```bash
python -m pipeline.orchestrate --once
# Or as a scheduled daemon:
python -m pipeline.orchestrate --schedule
```

## Cost expectations (rough)

| Step | Cost |
|---|---|
| Play Store (Apify) | actor-priced (typically ~$0.5–$1 / 1k reviews) |
| App Store  (Apify) | actor-priced (similar) |
| Reddit (Apify)  | ~$4 per 1,000 items — 6 tokens × $5 ≈ 7,500 items headroom |
| Twitter (Apify) | ~$0.40 per 1,000 items — effectively unlimited within budget |
| OpenAI embeddings (text-embedding-3-small) | ~$0.02 per 1M tokens — negligible |
| OpenAI extract (gpt-4o-mini) | ~$0.15/1M input, $0.60/1M output — ~$0.001 per snippet |
| OpenAI synthesize (gpt-4o) | ~40 calls per weekly run × ~$0.03 — < $2/week |

At 15k extracted snippets you're looking at **~$15 OpenAI + your Apify budget**.

## Deploy the dashboard (Streamlit Community Cloud)

1. Push `Part 1/` to a **public** GitHub repo (make sure `.env` stays gitignored).
2. Streamlit Cloud → New app → point to `dashboard/app.py`.
3. In Streamlit's **Secrets** panel paste the contents of your `.env`.
4. Streamlit's Postgres access: use a hosted Postgres (Neon / Supabase free tier). Update `DATABASE_URL` accordingly. Weaviate → use Weaviate Cloud Serverless (free tier) and swap `WEAVIATE_URL`.

## Health check

```bash
psql postgresql://zepto:zepto@localhost:5432/zepto_discovery -c "
  SELECT source, COUNT(*) FROM raw_snippets GROUP BY 1;
  SELECT COUNT(*) FROM filtered_snippets;
  SELECT intent, COUNT(*) FROM extracted_insights GROUP BY 1;
  SELECT COUNT(*), AVG(confidence) FROM insight_cards;
"
```
