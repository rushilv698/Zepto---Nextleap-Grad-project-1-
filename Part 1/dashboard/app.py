"""Zepto Discovery Engine — Streamlit dashboard.

Run locally (with Docker Postgres up):
    streamlit run dashboard/app.py
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from dashboard.data import has_table, is_demo_mode, query

st.set_page_config(page_title="Zepto Discovery Engine", layout="wide")


# ==================================================================
# Constants / helpers
# ==================================================================
GOAL = "Increase the % of Monthly Active Customers who buy from at least one new category every month."

# Any insight/theme surfaced in the "main" tabs uses this taxonomy version.
# v2.1 = expansion-behavior focused (goal-aligned). v2 (broad) is shown only
# in the Methodology tab for comparison.
PRIMARY_TAX_V = 2   # v2.1

RATING_LABEL = {"high": "🟢 High", "medium": "🟡 Medium", "low": "🔴 Low"}
STATUS_LABEL = {
    "confirmed":   "🟢 Confirmed",
    "exploratory": "🟡 Exploratory",
    "revising":    "🟠 Needs revision",
    "shelved":     "🔴 Shelved",
}


def _parse_json(v):
    if isinstance(v, (dict, list)):
        return v
    if isinstance(v, str) and v.strip().startswith(("{", "[")):
        try:
            return json.loads(v)
        except Exception:
            return None
    return None


def _rating_from_confidence(conf: float | None) -> str:
    if conf is None or pd.isna(conf):
        return "low"
    if conf >= 65:
        return "high"
    if conf >= 40:
        return "medium"
    return "low"


# ==================================================================
# Cached queries
# ==================================================================
@st.cache_data(ttl=300)
def load_raw_counts() -> pd.DataFrame:
    return query("SELECT source, brand, n FROM raw_counts ORDER BY n DESC")


@st.cache_data(ttl=300)
def load_totals() -> dict:
    if has_table("raw_totals"):
        df = query("SELECT * FROM raw_totals LIMIT 1")
        if not df.empty:
            r = df.iloc[0]
            return {"raw": int(r.get("raw", 0)), "filtered": int(r.get("filtered", 0)),
                    "extracted": int(r.get("extracted", 0)), "cards": int(r.get("cards", 0))}
    return {"raw": 0, "filtered": 0, "extracted": 0, "cards": 0}


@st.cache_data(ttl=300)
def load_snippet_quality() -> pd.DataFrame:
    if not has_table("snippet_quality"):
        return pd.DataFrame()
    return query(
        "SELECT snippet_id, lang, is_spam, is_relevant, dup_of, "
        "info_value_score, is_expansion_relevant FROM snippet_quality"
    )


@st.cache_data(ttl=300)
def load_themes(taxonomy_version: int) -> pd.DataFrame:
    return query("""
        SELECT t.id, t.name, t.definition, t.status, t.parent_id,
               (SELECT name FROM themes p WHERE p.id = t.parent_id) AS parent_name,
               (SELECT COUNT(*) FROM review_themes rt WHERE rt.theme_id = t.id
                  AND rt.taxonomy_version = :v) AS members
        FROM themes t
        WHERE t.merged_into IS NULL AND t.taxonomy_version = :v
        ORDER BY parent_id NULLS FIRST, members DESC
    """, params={"v": int(taxonomy_version)})


@st.cache_data(ttl=300)
def load_v2_insights() -> pd.DataFrame:
    if not has_table("insights_v2"):
        return pd.DataFrame()
    return query("""
        SELECT id, theme_id, theme, taxonomy_version, hypothesis, one_line, detailed,
               suggested_experiment, part_2_probe,
               confidence, validation_status, critic_verdict, critic_notes,
               confidence_breakdown
        FROM insights_v2
        ORDER BY confidence DESC NULLS LAST
    """)


@st.cache_data(ttl=300)
def load_corpus_hypotheses() -> pd.DataFrame:
    if not has_table("corpus_hypotheses"):
        return pd.DataFrame()
    return query("""
        SELECT rank, title, claim, reasoning, grounded_in,
               counter_evidence_that_would_disprove, confidence, novelty,
               implication_for_zepto, interview_probe,
               top_line_read, what_this_corpus_cannot_answer,
               recommended_next_data_collection
        FROM corpus_hypotheses ORDER BY rank
    """)


@st.cache_data(ttl=300)
def load_v1_cards() -> pd.DataFrame:
    return query("SELECT * FROM insight_cards ORDER BY confidence DESC") if has_table("insight_cards") else pd.DataFrame()


@st.cache_data(ttl=300)
def load_v3_cards() -> pd.DataFrame:
    return query("SELECT * FROM insight_cards_v3 ORDER BY confidence DESC") if has_table("insight_cards_v3") else pd.DataFrame()


# ==================================================================
# Page header
# ==================================================================
st.title("Zepto AI-Powered Discovery Engine")
st.markdown(f"**Strategic goal:** {GOAL}")
st.caption(
    "This engine ingests public user feedback (Play Store reviews, Reddit, YouTube comments, news), "
    "filters aggressively for signal, discovers themes bottom-up from the data, generates hypotheses "
    "grounded in cited evidence, and validates them through five independent quality checks + an "
    "LLM Critic Agent."
)
if is_demo_mode():
    st.info(
        "📦 **Demo mode** — reading pre-computed snapshots. "
        "See the [source repo](https://github.com/rushilv698/Zepto---Nextleap-Grad-project-1-) "
        "for the live pipeline."
    )

TOTALS = load_totals()

# ==================================================================
# Tabs
# ==================================================================
tab_dash, tab_raw, tab_filter, tab_themes, tab_insights, tab_quality, tab_method = st.tabs([
    "📊 Dashboard",
    "🗂️ Raw data acquired",
    "🚿 Filtered data",
    "🧩 Themes generated",
    "💡 Insights generated",
    "✅ Quality validated",
    "🔬 Methodology & versions",
])


# ==================================================================
# 1) DASHBOARD — the story
# ==================================================================
with tab_dash:
    hyps = load_corpus_hypotheses()
    v2 = load_v2_insights()
    primary = v2[v2["taxonomy_version"] == PRIMARY_TAX_V] if not v2.empty else pd.DataFrame()

    st.markdown("### The story in one page")

    # Top-line
    if not hyps.empty:
        st.info(f"**Top-line reading of the corpus:**  {hyps['top_line_read'].iloc[0]}")

    st.markdown("---")

    # Section 1 — Why users don't explore (Reasoning layer)
    if not hyps.empty:
        st.markdown("#### 🎯 Why users don't explore new categories on Zepto")
        st.caption(
            "Five causal hypotheses inferred from the whole corpus — including from what "
            "users DON'T say. These are the *why* answers to the strategic goal."
        )
        for _, h in hyps.iterrows():
            with st.container(border=True):
                st.subheader(f"{h['title']}  ·  {RATING_LABEL.get(h['confidence'], '⚪')}")
                st.markdown(f"**Claim:** {h['claim']}")
                st.markdown(f"**Implication for Zepto:** {h['implication_for_zepto']}")
                st.markdown(f"**Interview probe:** _{h['interview_probe']}_")
                with st.expander("Reasoning + counter-evidence check"):
                    st.markdown(f"**Reasoning:** {h['reasoning']}")
                    st.markdown(f"**Counter-evidence that would disprove:** {h['counter_evidence_that_would_disprove']}")

    st.markdown("---")

    # Section 2 — Specific, evidence-linked insights
    if not primary.empty:
        st.markdown("#### 🧭 Specific hypotheses with cited evidence")
        st.caption(
            "Insights generated from the theme cluster of snippets that describe exploration, "
            "hesitation, or decision-making. Each has cited supporting quotes, an experiment "
            "suggestion, and an interview probe. Every one was reviewed by an independent LLM Critic."
        )
        for _, r in primary.iterrows():
            rating = _rating_from_confidence(r["confidence"])
            with st.container(border=True):
                st.subheader(
                    f"{r['theme']}  ·  {RATING_LABEL[rating]}  ·  "
                    f"{STATUS_LABEL.get(r['validation_status'], r['validation_status'])}"
                )
                st.markdown(f"**Hypothesis:** {r['hypothesis']}")
                if r["one_line"]:
                    st.markdown(f"_{r['one_line']}_")
                with st.expander("Detailed reasoning"):
                    st.write(r["detailed"])
                if r["suggested_experiment"]:
                    st.markdown(f"**Suggested 2-week experiment:** {r['suggested_experiment']}")
                if r["part_2_probe"]:
                    st.markdown(f"**Interview probe:** _{r['part_2_probe']}_")

    if hyps.empty and primary.empty:
        st.warning("No insights yet.")


# ==================================================================
# 2) RAW DATA ACQUIRED
# ==================================================================
with tab_raw:
    st.markdown("### Raw data acquired")
    st.caption(
        f"**{TOTALS['raw']:,} snippets** collected from four public sources across four Indian "
        "quick-commerce brands."
    )

    raw = load_raw_counts()
    zepto_n = int(raw[raw["brand"] == "zepto"]["n"].sum()) if not raw.empty else 0
    comp_n = int(raw[raw["brand"] != "zepto"]["n"].sum()) if not raw.empty else 0

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total snippets", f"{TOTALS['raw']:,}")
    col2.metric("Zepto (primary)", f"{zepto_n:,}",
                delta=f"{100*zepto_n/max(TOTALS['raw'],1):.0f}%" if TOTALS["raw"] else None)
    col3.metric("Competitors (control)", f"{comp_n:,}",
                delta=f"{100*comp_n/max(TOTALS['raw'],1):.0f}%" if TOTALS["raw"] else None)
    col4.metric("Languages", "en / hi / hinglish")

    with st.expander(
        "❓ Why did we collect competitor data if the project is about Zepto?",
        expanded=False,
    ):
        st.markdown(f"""
The project is about Zepto — **{zepto_n:,} of {TOTALS['raw']:,} snippets ({100*zepto_n/max(TOTALS['raw'],1):.0f}%) are Zepto**.
The other **{comp_n:,} ({100*comp_n/max(TOTALS['raw'],1):.0f}%) are three competitors** (Blinkit, BigBasket, Swiggy Instamart)
and serve a **specific analytical purpose**:

1. **Cross-source validation (a quality check).** For an insight to be tagged
   `confirmed` in Quality Validated, its supporting evidence must span at least
   2 sources AND 2 brands. Without competitor data, this check is meaningless.

2. **Zepto-specific vs industry-wide.** If users complain about the same thing on
   all four apps, it's an industry-level friction — not a Zepto opportunity.

3. **Real user comparison behavior.** Users express category-exploration mental
   models while comparing brands ("I use Zepto for groceries but Blinkit for
   produce"). That comparison is the mental model we want. It only appears when
   both brands are named.

Competitor data is a **control group**, deliberately smaller than the Zepto
primary set. It is *not* used to make claims about the competitors themselves.
""")

    if not raw.empty:
        st.markdown("#### Breakdown by source × brand")
        pivot = raw.pivot_table(index="source", columns="brand", values="n", fill_value=0)
        pivot["TOTAL"] = pivot.sum(axis=1)
        pivot.loc["TOTAL"] = pivot.sum(axis=0)
        st.dataframe(pivot, use_container_width=True)

        c1, c2, c3 = st.columns(3)
        with c1:
            by_source = raw.groupby("source", as_index=False)["n"].sum().sort_values("n", ascending=False)
            st.plotly_chart(px.pie(by_source, values="n", names="source", title="By source", hole=0.4),
                            use_container_width=True)
        with c2:
            by_brand = raw.groupby("brand", as_index=False)["n"].sum().sort_values("n", ascending=False)
            st.plotly_chart(px.pie(by_brand, values="n", names="brand", title="By brand", hole=0.4),
                            use_container_width=True)
        with c3:
            role = pd.DataFrame([{"role": "Zepto (primary)", "n": zepto_n},
                                 {"role": "Competitors (control)", "n": comp_n}])
            st.plotly_chart(
                px.pie(role, values="n", names="role", title="Primary vs control", hole=0.4,
                       color_discrete_map={"Zepto (primary)": "#8B33F7",
                                           "Competitors (control)": "#B4B4C1"}),
                use_container_width=True,
            )

    st.markdown("#### How each source was collected")
    st.markdown("""
**📱 Play Store** *(free direct library)* — Zepto, Blinkit, BigBasket, Swiggy Instamart. India, English, newest reviews.

**💬 Reddit** *(Apify)* — 30 India-focused subreddits + 25 broad-context search terms (`quick commerce india`, `kirana vs online`, `grocery habits`, etc.).

**📺 YouTube comments** *(free)* — 36 curated videos: Zepto Cafe launch, founder interviews, comparison reviews, business-model breakdowns, grocery hauls.

**📰 News + business media** *(free, via Google News RSS)* — 12 Zepto queries, real article URLs decoded and scraped from 15+ preferred publishers (Inc42, YourStory, LiveMint, Business Standard, Moneycontrol, NDTV Profit, and more).

**❌ Skipped** — Twitter/X and App Store (paid gates / broken endpoints; documented honestly).
""")


# ==================================================================
# 3) FILTERED DATA
# ==================================================================
with tab_filter:
    st.markdown("### Filtered data — 8-layer sequential filtration")
    st.caption(
        "Every snippet passes through eight sequential filters before it can influence any "
        "downstream analysis. Each layer removes a specific kind of noise or down-weights low-value signal."
    )

    sq = load_snippet_quality()
    if sq.empty:
        st.warning("Filtration hasn't run yet.")
    else:
        raw = TOTALS["raw"]
        lang_counted = int(sq["lang"].notna().sum())
        spam = int(sq["is_spam"].fillna(False).sum())
        irrelevant = int(((sq["is_spam"] == False) & (sq["is_relevant"] == False)).sum())
        dupes = int(sq["dup_of"].notna().sum())
        keepers_broad = int(((sq["is_spam"] == False) & (sq["is_relevant"] == True) & (sq["dup_of"].isna())).sum())
        keepers_expansion = int(((sq["is_spam"] == False) & (sq["dup_of"].isna()) &
                                 (sq["is_expansion_relevant"] == True)).sum())

        st.markdown("#### Filtration funnel")
        fig = go.Figure(go.Funnel(
            y=[
                "Raw scraped",
                "Language classified",
                "Not spam",
                "Relevant to shopping behavior",
                "Unique (not near-dup)",
                "Describes exploration behavior",
            ],
            x=[
                raw, lang_counted,
                lang_counted - spam,
                lang_counted - spam - irrelevant,
                keepers_broad,
                keepers_expansion,
            ],
            textinfo="value+percent initial",
        ))
        fig.update_layout(margin=dict(l=0, r=0, t=0, b=0), height=380)
        st.plotly_chart(fig, use_container_width=True)

        st.markdown("#### Per-layer detail")
        rows = [
            ("1. Language & Normalization",
             "FastText language detection + brand-name canonicalization.",
             f"{lang_counted:,} classified"),
            ("2. Spam & Bot Detection",
             "Regex fast-path + DeepSeek LLM on borderline rows.",
             f"❌ {spam:,} dropped ({100*spam/raw:.1f}%)"),
            ("3. Relevance Filter",
             "Snippet must actually discuss shopping behavior / discovery / habits — not just delivery bugs.",
             f"❌ {irrelevant:,} more dropped"),
            ("4. Semantic Deduplication",
             "Cosine similarity ≥ 0.95 against every earlier embedding.",
             f"❌ {dupes:,} near-duplicates flagged"),
            ("5. Behaviour Filter",
             "7 boolean flags per snippet: routine, repeat-purchase, exploration, trust, hesitation, price-sensitivity, decision-process.",
             "Weighting input"),
            ("6. Specificity Filter",
             "1–5 rating on detail level. 'Good app' → 1; specific product story → 5.",
             "Weighting input"),
            ("7. Information Value Score",
             "Composite: 0.30·specificity + 0.20·novelty + 0.20·behavioural + 0.15·clarity + 0.15·actionability.",
             f"{int(sq['info_value_score'].notna().sum()):,} scored"),
            ("8. Temporal / Regional weighting",
             "Recency decay (half-life 180d) + city where inferable. Applied at scoring time.",
             "Downstream weight"),
        ]
        st.dataframe(pd.DataFrame(rows, columns=["Layer", "What it does", "Result"]),
                     hide_index=True, use_container_width=True)

        st.markdown("#### Snippets that survived — used for theme discovery")
        c1, c2 = st.columns(2)
        c1.metric("Broad-relevance keepers", f"{keepers_broad:,}",
                  help="Everything that survived filters 1–4. Available for R&D comparison (see Methodology tab).")
        c2.metric("Goal-aligned keepers", f"{keepers_expansion:,}",
                  help="Restricted to snippets whose behaviour flags describe exploration / hesitation / "
                       "decision-process. This is the set used for the primary insights.")


# ==================================================================
# 4) THEMES GENERATED
# ==================================================================
with tab_themes:
    st.markdown("### Themes generated")
    st.caption(
        "Themes emerge dynamically from the data (seed → grow → consolidate) rather than being "
        "pre-defined. 12 initial themes come from a 200-review sample; snippets that don't match "
        "any existing theme go into a candidate pool and are periodically clustered into new themes. "
        "Near-duplicate themes are auto-merged after every 500 assignments."
    )

    with st.expander("How to read this — parent buckets vs leaf themes", expanded=True):
        st.markdown("""
Themes are organized in a **two-level hierarchy** so both high-level reporting and detailed
behavioral nuances stay visible:

- **Parent buckets** — the *big categories* a review can fall into. Broad, stable, and
  easy to scan at a glance. Example: **"Value Perception & Price Sensitivity"**.
  Reporting stays consistent even as new leaf themes emerge underneath.

- **Leaf themes** — the *specific behaviours* underneath each parent. This is
  where the actionable pattern lives. Example: **"Price Sensitivity and Perceived Value"**
  nested under Value Perception & Price Sensitivity, or **"Repeat-Order Habit Loops"**
  nested under User Behavior & Decision Drivers.

Two types of leaf themes:
- **seed** — one of the original 12 themes generated by the LLM from a 200-review sample.
- **emergent** — discovered later by the DBSCAN clustering step from snippets that didn't
  fit any existing theme. These are the *new patterns the pipeline surfaced without being told to look for them*.

**Insights are generated per leaf theme, not per parent bucket** — the parent is just an
organizing shelf, the leaf is where the story lives.
""")

    th = load_themes(PRIMARY_TAX_V)
    if th.empty:
        st.info("No themes yet.")
    else:
        n_parents = int(th["parent_id"].isna().sum())
        n_leaves = int(th["parent_id"].notna().sum())
        promoted = int((th["status"] == "promoted").sum())

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total themes", len(th),
                  help="Parent buckets + leaf themes combined.")
        c2.metric("Parent buckets", n_parents,
                  help="High-level categories that organize the leaf themes into stable reporting shelves.")
        c3.metric("Leaf themes", n_leaves,
                  help="The specific behaviours we surface insights from — nested under parent buckets.")
        c4.metric("Emergent from data", promoted,
                  help="Leaf themes discovered by DBSCAN clustering the candidate pool — not part of the original 12 seed themes.")

        st.markdown("#### Themes by supporting-review count")
        leaves = th[th["members"] > 0].sort_values("members", ascending=True).tail(20)
        if not leaves.empty:
            fig = px.bar(
                leaves, x="members", y="name", color="parent_name",
                orientation="h",
                labels={"members": "# supporting reviews", "name": "theme", "parent_name": "Parent bucket"},
                height=500,
            )
            fig.update_layout(yaxis=dict(tickmode="linear"), margin=dict(l=0, r=0, t=10, b=0))
            st.plotly_chart(fig, use_container_width=True)

        st.markdown("#### Hierarchy — parent buckets → leaf themes")
        parents = th[th["parent_id"].isna()]
        for _, p in parents.iterrows():
            children = th[th["parent_id"] == p["id"]].sort_values("members", ascending=False)
            total_members = int(children["members"].sum())
            with st.expander(
                f"**{p['name']}**  ·  {len(children)} themes  ·  {total_members} supporting reviews",
                expanded=(total_members > 15),
            ):
                if p["definition"]:
                    st.caption(p["definition"])
                for _, c in children.iterrows():
                    badge = "seed" if c["status"] == "seed" else "emergent"
                    st.markdown(f"- **{c['name']}**  ·  {c['members']} reviews  ·  `{badge}`")
                    if c["definition"]:
                        st.caption(f"  _{c['definition']}_")


# ==================================================================
# 5) INSIGHTS GENERATED
# ==================================================================
with tab_insights:
    st.markdown("### Insights generated")
    st.caption(
        "For every theme with enough supporting reviews, the engine generated a hypothesis with "
        "cited evidence, a suggested 2-week experiment, and an interview probe. All insights are "
        "ranked by confidence."
    )

    v2 = load_v2_insights()
    primary = v2[v2["taxonomy_version"] == PRIMARY_TAX_V] if not v2.empty else pd.DataFrame()
    hyps = load_corpus_hypotheses()

    total = len(primary) + len(hyps)
    c1, c2, c3 = st.columns(3)
    c1.metric("Total insights", total)
    c2.metric("Corpus-level hypotheses", len(hyps),
              help="Generated by GPT-4.1 reasoning across the whole corpus, including from what's ABSENT.")
    c3.metric("Theme-level insights", len(primary),
              help="Generated per theme with cited evidence + LLM Critic review.")

    st.markdown("---")
    st.markdown("#### Corpus-level hypotheses  (the *why*)")
    if hyps.empty:
        st.info("None yet.")
    else:
        for _, h in hyps.iterrows():
            with st.container(border=True):
                st.subheader(f"{h['title']}  ·  {RATING_LABEL.get(h['confidence'], '⚪')}")
                st.markdown(f"**Claim:** {h['claim']}")
                st.markdown(f"**Implication for Zepto:** {h['implication_for_zepto']}")
                st.markdown(f"**Interview probe:** _{h['interview_probe']}_")
                with st.expander("Full reasoning"):
                    st.markdown(f"**Reasoning:** {h['reasoning']}")
                    st.markdown(f"**Counter-evidence that would disprove:** {h['counter_evidence_that_would_disprove']}")

    st.markdown("---")
    st.markdown("#### Theme-level insights  (evidence-linked, critic-reviewed)")
    if primary.empty:
        st.info("None yet.")
    else:
        for _, r in primary.iterrows():
            rating = _rating_from_confidence(r["confidence"])
            with st.container(border=True):
                st.subheader(
                    f"{r['theme']}  ·  {RATING_LABEL[rating]}  ·  "
                    f"{STATUS_LABEL.get(r['validation_status'], r['validation_status'])}"
                )
                st.markdown(f"**Hypothesis:** {r['hypothesis']}")
                st.write(r["detailed"])
                if r["suggested_experiment"]:
                    st.markdown(f"**Suggested 2-week experiment:** {r['suggested_experiment']}")
                if r["part_2_probe"]:
                    st.markdown(f"**Interview probe:** _{r['part_2_probe']}_")


# ==================================================================
# 6) QUALITY VALIDATED
# ==================================================================
with tab_quality:
    st.markdown("### Insight quality — multi-layer validation")
    st.caption(
        "Every theme-level insight was scored on 5 automated validation checks plus an independent "
        "LLM Critic Agent (a different model from the generator) that reviewed each insight against "
        "retrieved counter-evidence."
    )

    v2 = load_v2_insights()
    primary = v2[v2["taxonomy_version"] == PRIMARY_TAX_V] if not v2.empty else pd.DataFrame()
    if primary.empty:
        st.warning("No insights yet.")
    else:
        st.markdown("#### The five automated checks")
        checks = pd.DataFrame(
            [
                ("Evidence", "≥ 5 supporting review IDs must be cited by the generator.",
                 "Below threshold → shelved."),
                ("Cross-source", "Evidence must span ≥ 2 sources AND ≥ 2 brands.",
                 "Confirmed only if diverse enough."),
                ("Statistical", "Theme needs ≥ 20 unique authors AND ≥ 0.5% of corpus.",
                 "Prevents fringe hypotheses."),
                ("Cluster quality", "Theme intra-cluster mean cosine ≥ 0.70.",
                 "Signal must be coherent."),
                ("LLM Critic Agent", "GPT-4.1 reviews DeepSeek's output + 10 retrieved counter-quotes.",
                 "Verdict: pass / revise / reject."),
            ],
            columns=["Layer", "Rule", "Enforcement"],
        )
        st.dataframe(checks, hide_index=True, use_container_width=True)

        # Compute validation matrix
        rows = []
        for _, r in primary.iterrows():
            bd = _parse_json(r["confidence_breakdown"]) or {}
            rating = _rating_from_confidence(r["confidence"])
            rows.append({
                "theme": r["theme"],
                "rating": RATING_LABEL[rating],
                "confidence_score": r["confidence"],
                "status": STATUS_LABEL.get(r["validation_status"], r["validation_status"]),
                "evidence": "✓" if bd.get("evidence_pass") else "✗",
                "cross_source": "✓" if bd.get("cross_source_pass") else "✗",
                "sources": bd.get("evidence_source_count", "-"),
                "brands": bd.get("evidence_brand_count", "-"),
                "statistical": "✓" if bd.get("statistical_pass") else "✗",
                "unique_authors": bd.get("unique_authors", "-"),
                "cluster_quality": "✓" if bd.get("cluster_quality_pass") else "✗",
                "intra_cosine": bd.get("intra_cluster_sim", "-"),
                "critic": bd.get("critic_verdict") or r["critic_verdict"] or "-",
            })
        vm = pd.DataFrame(rows)

        n_confirmed = int((primary["validation_status"] == "confirmed").sum())
        n_exploratory = int((primary["validation_status"] == "exploratory").sum())
        n_shelved = int((primary["validation_status"] == "shelved").sum())

        st.markdown("#### Per-insight validation matrix")
        st.dataframe(vm, hide_index=True, use_container_width=True)

        st.markdown("#### Usability summary")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total insights", len(primary))
        c2.metric("🟢 Confirmed", n_confirmed,
                  help="Cleared all hard checks AND critic verdict = pass.")
        c3.metric("🟡 Exploratory", n_exploratory,
                  help="Some checks missed or critic said 'revise'. Real signal but needs more grounding.")
        c4.metric("🔴 Shelved", n_shelved,
                  help="Critic rejected OR failed hard checks. Kept for transparency.")

        st.warning(
            "**Honest gaps:** Behavioural validation (recommendation CTR, category visits, repeat "
            "purchases) and Business & Experiment validation (A/B uplift) require Zepto internal "
            "analytics we don't have. They're flagged `not_evaluated` and `deferred_to_part_4` in "
            "every insight's confidence breakdown — not silently skipped."
        )

        st.markdown("#### How to use these insights")
        if n_confirmed > 0:
            st.success(f"**{n_confirmed} confirmed insights** — primary drivers, ready for the deck.")
        if n_exploratory > 0:
            st.info(
                f"**{n_exploratory} exploratory insights** — real signal, needs one more layer of "
                "evidence. **Ideal Part 2 interview subjects** — every one has an open-ended "
                "interview probe generated for it."
            )
        if n_shelved > 0:
            st.error(
                f"**{n_shelved} shelved insights** — critic rejected. Shown for transparency, "
                "not for product decisions."
            )


# ==================================================================
# 7) METHODOLOGY & VERSIONS
# ==================================================================
with tab_method:
    st.markdown("### Methodology & versions")
    st.caption(
        "For anyone auditing the work: this engine went through multiple methodology iterations "
        "before landing on the primary approach used in the Dashboard / Insights tabs. Older "
        "versions are preserved for comparison."
    )

    st.markdown("#### Why four versions exist")
    st.markdown("""
The first two attempts (v1 and v3) use a **fixed-taxonomy, post-hoc clustering** approach —
extract labels from every review against a pre-defined 9-barrier list, then DBSCAN the
embeddings. The problem: real users don't complain in those 9 categories, so the LLM
force-fits noise into the wrong boxes.

The next two (v2 and v2.1) use the **PDF methodology** — an 8-layer filter, an evolving
taxonomy that grows from the data itself, evidence-linked hypotheses, and a 5-check
validation gate. Different pipeline in every phase.

**v2.1** is what powers the primary dashboard because it filters the candidate universe to
snippets that describe exploration / hesitation / decision-making — directly on the goal
of "increase % of MACs who buy from a new category". v2 (broad relevance) is retained
for comparison; its insights are richer in trust/price/service texture but drift off-goal.
""")

    st.markdown("#### Version comparison")
    ver_table = pd.DataFrame([
        {"Version": "v1",   "Filtration": "1-layer keyword",   "Taxonomy": "Fixed 9-barrier",   "Validation": "Confidence score only",              "Cards / insights": "7 cards"},
        {"Version": "v3",   "Filtration": "1-layer keyword",   "Taxonomy": "Fixed 9-barrier",   "Validation": "+ counter-evidence hint (prompt)",  "Cards / insights": "30 cards"},
        {"Version": "v2",   "Filtration": "8-layer",           "Taxonomy": "Evolving (broad, 910 rows)",     "Validation": "5-check gate + LLM Critic",           "Cards / insights": "9 insights"},
        {"Version": "v2.1", "Filtration": "8-layer",           "Taxonomy": "Evolving (expansion-only, 546)", "Validation": "5-check gate + LLM Critic",           "Cards / insights": "3 insights ← primary"},
    ])
    st.dataframe(ver_table, hide_index=True, use_container_width=True)

    st.markdown("#### v2 (broad-relevance) insights — for comparison")
    v2 = load_v2_insights()
    v2_broad = v2[v2["taxonomy_version"] == 1] if not v2.empty else pd.DataFrame()
    if v2_broad.empty:
        st.info("No v2 broad insights.")
    else:
        for _, r in v2_broad.iterrows():
            rating = _rating_from_confidence(r["confidence"])
            with st.container(border=True):
                st.subheader(f"{r['theme']}  ·  {RATING_LABEL[rating]}  ·  {STATUS_LABEL.get(r['validation_status'], r['validation_status'])}")
                st.markdown(f"**Hypothesis:** {r['hypothesis']}")
                with st.expander("Detail + experiment"):
                    st.write(r["detailed"])
                    if r["suggested_experiment"]:
                        st.markdown(f"**Experiment:** {r['suggested_experiment']}")

    st.markdown("#### v1 + v3 (fixed-taxonomy) cards — earliest R&D output")
    v1 = load_v1_cards()
    v3 = load_v3_cards()
    if not v1.empty:
        with st.expander(f"v1 — {len(v1)} cards"):
            for _, r in v1.iterrows():
                rating = _rating_from_confidence(r["confidence"])
                st.markdown(f"**{r['title']}**  ·  {RATING_LABEL[rating]}")
                st.caption(r["one_line"])
    if not v3.empty:
        with st.expander(f"v3 — {len(v3)} cards"):
            for _, r in v3.iterrows():
                rating = _rating_from_confidence(r["confidence"])
                st.markdown(f"**{r['title']}**  ·  {RATING_LABEL[rating]}")
                st.caption(r["hypothesis"])
