-- Zepto Discovery Engine — Postgres schema
-- Run: psql $DATABASE_URL -f db/schema.sql

CREATE TABLE IF NOT EXISTS raw_snippets (
    id              TEXT PRIMARY KEY,           -- sha256 of (source || normalized_text)
    source          TEXT NOT NULL,              -- play_store | app_store | reddit | twitter | youtube | forum
    source_url      TEXT,
    text            TEXT NOT NULL,
    text_normalized TEXT NOT NULL,
    author          TEXT,
    posted_at       TIMESTAMPTZ,
    lang            TEXT,
    rating          INT,                        -- 1..5 for app stores; null elsewhere
    raw_metadata    JSONB,
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS raw_snippets_source_idx     ON raw_snippets(source);
CREATE INDEX IF NOT EXISTS raw_snippets_posted_at_idx  ON raw_snippets(posted_at);

CREATE TABLE IF NOT EXISTS extracted_insights (
    id                        BIGSERIAL PRIMARY KEY,
    snippet_id                TEXT NOT NULL REFERENCES raw_snippets(id) ON DELETE CASCADE,
    intent                    TEXT NOT NULL,
    themes                    TEXT[] NOT NULL DEFAULT '{}',
    user_persona              TEXT,
    category_currently_buying TEXT,
    category_avoiding         TEXT,
    barrier_summary           TEXT,
    emotional_tone            TEXT,
    actionable_quote          BOOLEAN DEFAULT FALSE,
    suggested_intervention    TEXT,
    prompt_version            TEXT NOT NULL,
    model                     TEXT NOT NULL,
    raw_response              JSONB,
    extracted_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (snippet_id, prompt_version)
);
CREATE INDEX IF NOT EXISTS extracted_insights_intent_idx  ON extracted_insights(intent);
CREATE INDEX IF NOT EXISTS extracted_insights_themes_idx  ON extracted_insights USING GIN(themes);

CREATE TABLE IF NOT EXISTS macro_themes (
    id            BIGSERIAL PRIMARY KEY,
    cluster_key   TEXT NOT NULL,               -- DBSCAN cluster id, stable within a run
    run_id        TEXT NOT NULL,
    label         TEXT,                        -- human-readable label (may be null until synthesized)
    member_count  INT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (run_id, cluster_key)
);

CREATE TABLE IF NOT EXISTS macro_theme_members (
    macro_theme_id BIGINT NOT NULL REFERENCES macro_themes(id) ON DELETE CASCADE,
    insight_id     BIGINT NOT NULL REFERENCES extracted_insights(id) ON DELETE CASCADE,
    PRIMARY KEY (macro_theme_id, insight_id)
);

CREATE TABLE IF NOT EXISTS insight_cards (
    id                     BIGSERIAL PRIMARY KEY,
    macro_theme_id         BIGINT REFERENCES macro_themes(id) ON DELETE SET NULL,
    title                  TEXT NOT NULL,
    one_line               TEXT NOT NULL,
    detailed               TEXT,
    persona_most_affected  TEXT,
    primary_barrier        TEXT,
    suggested_experiment   TEXT,
    confidence             NUMERIC(5,2),        -- 0..100
    confidence_breakdown   JSONB,               -- {source_credibility, frequency_volume, ...}
    source_counts          JSONB,               -- {reddit: 62, play_store: 147, ...}
    unique_authors         INT,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    prompt_version         TEXT
);
CREATE INDEX IF NOT EXISTS insight_cards_confidence_idx ON insight_cards(confidence DESC);

CREATE TABLE IF NOT EXISTS hitl_reviews (
    insight_card_id  BIGINT NOT NULL REFERENCES insight_cards(id) ON DELETE CASCADE,
    reviewer         TEXT NOT NULL,
    actionability    INT CHECK (actionability BETWEEN 1 AND 5),
    alignment        INT CHECK (alignment BETWEEN 1 AND 5),
    notes            TEXT,
    reviewed_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (insight_card_id, reviewer)
);
