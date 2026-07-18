-- v2 schema — new tables introduced for the PDF methodology.
-- Kept separate from schema.sql to preserve v1/v3 tables intact.
-- Idempotent; safe to run repeatedly.

-- ── Filtration outputs (also created by pipeline.dedupe.py) ────────────────────
CREATE TABLE IF NOT EXISTS snippet_quality (
    snippet_id         TEXT PRIMARY KEY REFERENCES raw_snippets(id) ON DELETE CASCADE,
    lang               TEXT,
    is_spam            BOOLEAN,
    spam_kind          TEXT,
    is_relevant        BOOLEAN,
    dup_of             TEXT REFERENCES raw_snippets(id) ON DELETE SET NULL,
    behaviour_flags    JSONB,
    specificity        INT,
    clarity            INT,
    actionability      INT,
    novelty            NUMERIC(4,3),
    info_value_score   NUMERIC(5,2),
    weight_recency     NUMERIC(5,3),
    weight_region      TEXT,
    text_normalized_v2 TEXT,
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS snippet_quality_dup_idx   ON snippet_quality(dup_of);
CREATE INDEX IF NOT EXISTS snippet_quality_info_idx  ON snippet_quality(info_value_score DESC);
CREATE INDEX IF NOT EXISTS snippet_quality_spam_idx  ON snippet_quality(is_spam);

-- ── Theme taxonomy (evolving) ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS taxonomy_versions (
    version           INT PRIMARY KEY,
    promoted_count    INT DEFAULT 0,
    consolidated_count INT DEFAULT 0,
    snapshot_json     JSONB,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS themes (
    id                 BIGSERIAL PRIMARY KEY,
    name               TEXT NOT NULL,
    definition         TEXT,
    embedding_centroid NUMERIC[],           -- 1536-dim OpenAI embedding, stored as float array
    parent_id          BIGINT REFERENCES themes(id) ON DELETE SET NULL,
    taxonomy_version   INT REFERENCES taxonomy_versions(version),
    status             TEXT DEFAULT 'seed', -- seed | promoted | merged | archived
    merged_into        BIGINT REFERENCES themes(id) ON DELETE SET NULL,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS themes_parent_idx ON themes(parent_id);
CREATE INDEX IF NOT EXISTS themes_status_idx ON themes(status);

CREATE TABLE IF NOT EXISTS theme_candidates (
    snippet_id       TEXT PRIMARY KEY REFERENCES raw_snippets(id) ON DELETE CASCADE,
    info_value_score NUMERIC(5,2),
    entered_pool_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS review_themes (
    snippet_id       TEXT NOT NULL REFERENCES raw_snippets(id) ON DELETE CASCADE,
    theme_id         BIGINT NOT NULL REFERENCES themes(id) ON DELETE CASCADE,
    similarity       NUMERIC(4,3),
    assigned_by      TEXT NOT NULL,        -- seed | cosine | promotion | consolidation
    taxonomy_version INT REFERENCES taxonomy_versions(version),
    assigned_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (snippet_id, theme_id)
);
CREATE INDEX IF NOT EXISTS review_themes_theme_idx ON review_themes(theme_id);

-- ── Insights v2 + validation ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS insights_v2 (
    id                      BIGSERIAL PRIMARY KEY,
    theme_id                BIGINT REFERENCES themes(id) ON DELETE CASCADE,
    taxonomy_version        INT REFERENCES taxonomy_versions(version),
    hypothesis              TEXT NOT NULL,
    one_line                TEXT,
    detailed                TEXT,
    suggested_experiment    TEXT,
    part_2_probe            TEXT,
    generator_model         TEXT,
    generator_prompt_version TEXT,
    generated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    confidence              NUMERIC(5,2),
    confidence_breakdown    JSONB,
    validation_status       TEXT DEFAULT 'exploratory',  -- exploratory | confirmed | revising | shelved
    critic_verdict          TEXT,                          -- pass | revise | reject
    critic_notes            TEXT,
    hitl_verdict            TEXT,
    hitl_notes              TEXT
);
CREATE INDEX IF NOT EXISTS insights_v2_theme_idx     ON insights_v2(theme_id);
CREATE INDEX IF NOT EXISTS insights_v2_conf_idx      ON insights_v2(confidence DESC);
CREATE INDEX IF NOT EXISTS insights_v2_status_idx    ON insights_v2(validation_status);

CREATE TABLE IF NOT EXISTS insight_evidence (
    insight_id       BIGINT NOT NULL REFERENCES insights_v2(id) ON DELETE CASCADE,
    snippet_id       TEXT NOT NULL REFERENCES raw_snippets(id) ON DELETE CASCADE,
    kind             TEXT NOT NULL DEFAULT 'supporting',  -- supporting | contradicting
    retrieval_score  NUMERIC(4,3),
    PRIMARY KEY (insight_id, snippet_id)
);
