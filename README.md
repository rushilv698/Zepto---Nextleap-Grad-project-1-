# Part 1 — Zepto AI-Powered Discovery Engine

NextLeap Grad Project 1 · Submission deadline: **4 August 2026**

## Context

As a PM on Zepto's Growth team, the strategic goal is to **increase the % of Monthly Active Customers who purchase from at least one new category every month**. Users are stuck in habitual buying loops (milk, bread, groceries) and rarely explore adjacent categories (personal care, pet supplies, baby products, snacks).

Part 1 builds an **AI-powered discovery engine** that ingests unstructured user feedback at scale and outputs quantified, confidence-scored insights + causal hypotheses about *why* exploration doesn't happen.

## Live pipeline state (this scrape)

| Layer | Count | Table |
|---|---|---|
| Raw snippets ingested | **11,406** | `raw_snippets` |
| Passed keyword/brand filter | **8,331** | `filtered_snippets` |
| Embedded (OpenAI + Weaviate) | **8,331** | `embedded_snippets` |
| First-pass structured extraction | **8,331** | `extracted_insights` |
| Discovery-focused deep signal | **8,331** | `discovery_signals` |
| **Insight cards v1** (simple synth) | 10 | `insight_cards` |
| **Insight cards v3** (adversarial + counter-evidence + interview probes) | 30 | `insight_cards_v3` |
| **Corpus hypotheses** (reasoning-layer) | 5 | `corpus_hypotheses` |

## Data sources scraped

| Source | Brand(s) | Rows | Notes |
|---|---|---|---|
| Google Play Store (direct library) | zepto, blinkit, bigbasket, swiggy_instamart | 8,283 | India, English |
| Reddit Apify actor | zepto (search + community + deep) | 1,194 | 10 India subreddits, 8 search terms, 10 deep-context terms |
| YouTube comment scraper | zepto ecosystem | 1,929 | 16 curated videos (Zepto Cafe, comparisons, founder interviews, quick-commerce breakdowns) |
| Twitter/X (Apify) | — | 0 | **Skipped** — both actors gated behind Apify paid plan |
| App Store | — | 0 | **Skipped** — Apify actor gated + direct lib broken |

## Stack decisions (final)

| Concern | Choice | Why |
|---|---|---|
| Extraction LLM | **DeepSeek-chat** (`deepseek-chat`) | ~10× faster than gpt-4o-mini (no OpenAI RPD limit), ~1/3 the cost, quality parity for JSON extraction |
| Synthesis LLM | **GPT-4.1** (`gpt-4.1`) | Best instruction-following for constrained taxonomy + adversarial reasoning |
| Embeddings | **OpenAI `text-embedding-3-small`** | DeepSeek has no embedding endpoint |
| Vector DB | **Weaviate** (local Docker) | Semantic clustering + near-dup detection |
| Structured store | **Postgres** (local Docker) | All extract / synth / reasoning outputs |
| Raw store | Local `data_lake/*.parquet` | Backup of every scrape |
| Dashboard | **Streamlit** (localhost:8501) | Free public link candidate for deliverable |
| Orchestration | **Python + APScheduler** | Faster to iterate than n8n |

## Three parallel synthesis approaches — kept separate for comparison

The pipeline runs three DIFFERENT approaches to turn signal into insights. All three coexist so you can compare and choose what to use in the deck.

### v1 — Simple synthesis (`pipeline/synthesize.py` → `insight_cards`)
DBSCAN cluster on embeddings → 1 GPT call per cluster → title / one-liner / suggested experiment. Fast, cheap, no counter-evidence.

**Run:** `python -m pipeline.synthesize`

### v3 — Adversarial synthesis (`pipeline/synthesize_v3.py` → `insight_cards_v3`)
Same clustering + BUT the prompt (`config/prompts/synthesize_v3.txt`) forces:
- Taxonomy-locked persona + barrier
- Explicit hypothesis (causal claim, not just theme)
- **Counter-evidence check** (what would disprove this)
- Confidence-in-hypothesis (low/medium/high) — model's own honest read
- **Part-2 interview probes** (3 open-ended questions per card)
- Integrates `discovery_signals` into the cluster material

**Run:** `python -m pipeline.synthesize_v3 --eps 0.32 --min-samples 4`

### Reasoning-layer — the decision engine (`pipeline/reason.py` → `corpus_hypotheses`)
**Different in kind**: instead of clustering similar rows, it feeds GPT-4.1 the **whole corpus statistical portrait** + 21 representative quotes and asks it to REASON ACROSS the corpus — including inferring signal from what's ABSENT (e.g., "97% of relevant snippets have zero exploration signal → that's the insight").

Emits 5 ranked non-obvious hypotheses with:
- Grounding (which specific patterns support it)
- Counter-evidence check
- Novelty flag (kills obvious hypotheses)
- Implication for Zepto (what to do)
- Interview probe for Part 2
- Honest "what this corpus cannot answer"
- Concrete next-data-collection recommendation

**Run:** `python -m pipeline.reason`

## Full pipeline — end-to-end

```bash
# ---- 1. Scrape ----
python -m scrapers.play_store  --max-reviews 5000
python -m scrapers.play_store  --competitors --per-app 2000
python -m scrapers.reddit      --mode search    --max-items 500
python -m scrapers.reddit      --mode community --max-items 500
python -m scrapers.reddit      --mode deep      --max-items 600
python -m scrapers.youtube     --per-video 300

# ---- 2. Process ----
python -m pipeline.filter
python -m pipeline.embed
python -m pipeline.extract           --limit 10000 --workers 12
python -m pipeline.extract_discovery --limit 10000 --workers 12
python -m pipeline.cooccurrence

# ---- 3. Three synthesis approaches (run any / all) ----
python -m pipeline.synthesize                            # v1 → insight_cards
python -m pipeline.synthesize_v3 --eps 0.32 --min-samples 4   # v3 → insight_cards_v3
python -m pipeline.reason                                # reasoning → corpus_hypotheses

# ---- 4. Dashboard ----
streamlit run dashboard/app.py       # http://localhost:8501
```

## Folder layout

```
Part 1/
├── README.md                        ← this file
├── QUICKSTART.md                    ← install + run commands
├── requirements.txt
├── docker-compose.yml               ← Postgres + Weaviate
├── .env / .env.example              ← keys (.env is gitignored)
├── config/
│   ├── sources.yaml                 ← subreddits, YouTube video IDs, competitor app IDs
│   ├── taxonomy.yaml                ← intents / barriers / personas / categories
│   └── prompts/
│       ├── extract_v1.txt           ← 1st-pass structured extraction
│       ├── extract_v2_discovery.txt ← 2nd-pass discovery-focused extraction
│       ├── synthesize_v1.txt        ← simple cluster synth
│       ├── synthesize_v2.txt        ← taxonomy-locked synth
│       ├── synthesize_v3.txt        ← adversarial + counter-evidence + interview probes
│       └── reason_v1.txt            ← corpus-level reasoning
├── scrapers/
│   ├── play_store.py                ← Zepto + competitors, direct library
│   ├── app_store.py                 ← disabled (Apple endpoint broken)
│   ├── reddit.py                    ← 3 modes: search / community / deep
│   ├── youtube.py                   ← curated video comment pull
│   └── twitter.py                   ← disabled (Apify paid gate)
├── pipeline/
│   ├── settings.py                  ← env + YAML loader
│   ├── apify_pool.py                ← round-robin over 6 Apify tokens
│   ├── storage.py                   ← Snippet + Parquet + Postgres upsert
│   ├── openai_client.py             ← OpenAI + DeepSeek routing, JSON mode
│   ├── filter.py                    ← keyword + brand pre-filter
│   ├── embed.py                     ← OpenAI embeddings → Weaviate
│   ├── extract.py                   ← 1st-pass extractor (DeepSeek, 12 workers)
│   ├── extract_discovery.py         ← 2nd-pass discovery-focused (DeepSeek, 12 workers)
│   ├── cooccurrence.py              ← category co-mention + gateway graph
│   ├── confidence.py                ← 5-factor score (source × freq × sent × clarity × cross-source)
│   ├── synthesize.py                ← v1 simple synth
│   ├── synthesize_v3.py             ← v3 adversarial synth
│   ├── reason.py                    ← corpus-level reasoning layer
│   └── orchestrate.py               ← APScheduler entrypoint
├── db/
│   └── schema.sql                   ← raw_snippets, extracted_insights, macro_themes, insight_cards, discovery_signals, category_edges, corpus_hypotheses, hitl_reviews
├── dashboard/
│   └── app.py                       ← Streamlit
├── data_lake/                       ← gitignored raw Parquet
├── tests/
│   └── smoke_test.py                ← OpenAI + Apify + Postgres + Weaviate
└── notebooks/                       ← optional HITL review
```

## Key findings from this run (as of README write)

**Top-line (from reasoning layer):**
> Zepto users overwhelmingly display a one-dimensional, habitual use pattern, with almost no signals of curiosity or intent to explore new categories — suggesting that new categories are largely invisible in their mental model of the platform.

**5 non-obvious hypotheses (all `corpus_hypotheses` table):**
1. **Zepto = 'Grocery-Only' in User Minds** *(high confidence)* — new categories aren't refused, they're not considered
2. Invisible UI: New Categories Are Hidden in Plain Sight
3. **Trust Is Category-Specific, Not Platform-Wide** — grocery trust doesn't transfer to beauty/pet/baby
4. Habit Loop Reinforces Category Stagnation
5. Category Expansion Lacks Social Proof

**What the corpus CANNOT answer:**
- Actual in-app user journey / UI-element impact
- Latent curiosity that isn't expressed in public reviews
- Whether users see-and-ignore vs never-see new category tiles

**Recommended next data:** In-app event logs + session replays for grocery-only users over 30 days.

## Skipped / decisions log

- **App Store scraper (`scrapers/app_store.py`)**: disabled. Apify actor requires paid plan; direct `app-store-scraper` library is broken (Apple gated the endpoint). India is Android-dominant, so signal loss is limited.
- **Twitter/X**: both Apify actors are gated behind paid plans. Skipped.
- **JioMart Play Store**: original app id was wrong; replaced by Swiggy Instamart (`in.swiggy.android`) which carries Instamart within it.
- **`extract_v2_discovery` intentionally aggressive-on-`none`**: The prompt biases toward `exploration_signal=none` on operational complaints. This is why 97% show `none` — that IS the finding, not a bug.

## Costs (this run)

| Line item | Cost |
|---|---|
| Apify — Reddit (Zepto + deep + community) | ~$4 |
| Apify — Play Store store scraper actor | ~$0 (fell back to direct library — free) |
| Apify — Twitter | $0 (blocked) |
| OpenAI embeddings (~8k × 500 tokens) | ~$0.05 |
| DeepSeek — extract_v1 (8,331 calls) | ~$0.80 |
| DeepSeek — extract_discovery (8,331 calls) | ~$1.00 |
| GPT-4.1 — synthesize_v3 (30 cluster calls) | ~$1.50 |
| GPT-4.1 — reason (1 giant call ~13k input tokens) | ~$0.20 |
| **Total** | **~$7.60** |

## Deliverables checklist

- [x] Hosted dashboard (Streamlit) with strategic Q&A + insight cards + trends + raw explorer
- [ ] 1 slide in final 10-slide deck explaining engine architecture (**next**)
- [ ] Loom walkthrough of the workflow
- [ ] Deploy to Streamlit Community Cloud for public link

## Related documents

- `../Docs/NEXTLEAP GRADPROJECT 1 Mission Statement.docx` — official brief
- `../Docs/NEXT LEAP GRAD PROJECT 1 Execution plan.docx` — strategic write-up

