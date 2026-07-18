"""Zepto Discovery Engine — Streamlit dashboard.

Run locally (with Docker Postgres up):
    streamlit run dashboard/app.py

Deployed on Streamlit Community Cloud — automatically falls back to reading
Parquet snapshots in Part 1/demo_data/ when Postgres is unreachable.
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
# Helpers
# ==================================================================
def _parse_json(v):
    if isinstance(v, (dict, list)):
        return v
    if isinstance(v, str) and v.strip().startswith(("{", "[")):
        try:
            return json.loads(v)
        except Exception:
            return None
    return None


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
def load_themes_and_members(taxonomy_version: int) -> pd.DataFrame:
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
def load_insights_v2() -> pd.DataFrame:
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
    if not has_table("insight_cards"):
        return pd.DataFrame()
    return query("SELECT * FROM insight_cards ORDER BY confidence DESC")


@st.cache_data(ttl=300)
def load_v3_cards() -> pd.DataFrame:
    if not has_table("insight_cards_v3"):
        return pd.DataFrame()
    return query("SELECT * FROM insight_cards_v3 ORDER BY confidence DESC")


# ==================================================================
# Page header
# ==================================================================
st.title("Zepto AI-Powered Discovery Engine")
st.caption(
    "Strategic goal: increase the % of Monthly Active Customers who purchase from "
    "at least one new category every month. This engine ingests public user "
    "feedback at scale, filters it, discovers themes, generates hypotheses, and "
    "validates them through multiple independent checks."
)
if is_demo_mode():
    st.info(
        "📦 **Demo mode** — reading pre-computed snapshots. "
        "See the [source repo](https://github.com/rushilv698/Zepto---Nextleap-Grad-project-1-) "
        "for the live pipeline."
    )

TOTALS = load_totals()

# ==================================================================
# Tabs (in the exact order the user asked for)
# ==================================================================
tab_dash, tab_raw, tab_filter, tab_themes, tab_insights, tab_quality = st.tabs([
    "📊 Dashboard",
    "🗂️ Raw data acquired",
    "🚿 Filtered data",
    "🧩 Themes generated",
    "💡 Insights generated",
    "✅ Quality validated",
])


# ==================================================================
# 1) DASHBOARD — final insight cards + confidence
# ==================================================================
with tab_dash:
    st.markdown("### The story — best insights across every method, ranked by confidence")
    st.caption(
        "This engine ran **four synthesis approaches in parallel** so results could be compared. "
        "The best output — Reasoning Layer hypotheses + v2.1 expansion-focused insights — is shown below."
    )

    hyps = load_corpus_hypotheses()
    v2 = load_insights_v2()
    v2_1 = v2[v2["taxonomy_version"] == 2] if not v2.empty else pd.DataFrame()

    # Top-line read
    if not hyps.empty:
        top_line = hyps["top_line_read"].iloc[0]
        st.info(f"**Corpus top-line read:**  {top_line}")

    # -- Reasoning layer: 5 hypotheses (direct answers to the goal)
    if not hyps.empty:
        st.markdown("#### 🎯 Reasoning-layer hypotheses  (whole-corpus inference — the *why*)")
        for _, h in hyps.iterrows():
            emoji = {"high": "🟢", "medium": "🟡", "low": "🔴"}.get(h["confidence"], "⚪")
            nov = "✨ non-obvious" if h["novelty"] == "non_obvious" else "obvious"
            with st.container(border=True):
                st.subheader(f"#{h['rank']}  ·  {h['title']}  ·  {emoji} {h['confidence']}  ·  {nov}")
                st.markdown(f"**Claim:** {h['claim']}")
                with st.expander("Reasoning + counter-evidence check"):
                    st.markdown(f"**Reasoning:** {h['reasoning']}")
                    st.markdown(f"**Counter-evidence that would disprove:** {h['counter_evidence_that_would_disprove']}")
                st.markdown(f"**Implication for Zepto:** {h['implication_for_zepto']}")
                st.markdown(f"**Interview probe:** _{h['interview_probe']}_")

    # -- v2.1 insights: expansion-focused, evidence-linked
    if not v2_1.empty:
        st.markdown("#### 🧭 v2.1 insights  (expansion-focused, evidence-linked)")
        for _, r in v2_1.iterrows():
            badge = {"confirmed": "🟢 confirmed", "exploratory": "🟡 exploratory",
                     "shelved": "🔴 shelved", "revising": "🟠 revising"}.get(
                     r["validation_status"], r["validation_status"])
            with st.container(border=True):
                st.subheader(f"{r['theme']}  ·  conf {r['confidence']:.0f}  ·  {badge}")
                st.markdown(f"**Hypothesis:** {r['hypothesis']}")
                if r["one_line"]:
                    st.markdown(f"_{r['one_line']}_")
                with st.expander("Detailed reasoning"):
                    st.write(r["detailed"])
                if r["suggested_experiment"]:
                    st.markdown(f"**Suggested experiment:** {r['suggested_experiment']}")
                if r["part_2_probe"]:
                    st.markdown(f"**Interview probe:** _{r['part_2_probe']}_")

    if hyps.empty and v2_1.empty:
        st.warning("No insights yet.")


# ==================================================================
# 2) RAW DATA ACQUIRED
# ==================================================================
with tab_raw:
    st.markdown("### Raw data acquired")
    st.caption(
        f"**{TOTALS['raw']:,} snippets** collected from four public sources across four Indian "
        "quick-commerce brands. Data was captured over several weeks."
    )

    raw = load_raw_counts()

    # Compute Zepto vs competitor split
    zepto_n = int(raw[raw["brand"] == "zepto"]["n"].sum()) if not raw.empty else 0
    comp_n = int(raw[raw["brand"] != "zepto"]["n"].sum()) if not raw.empty else 0

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total snippets", f"{TOTALS['raw']:,}")
    col2.metric("Zepto (primary)", f"{zepto_n:,}",
                delta=f"{100*zepto_n/max(TOTALS['raw'],1):.0f}%" if TOTALS["raw"] else None)
    col3.metric("Competitors (control)", f"{comp_n:,}",
                delta=f"{100*comp_n/max(TOTALS['raw'],1):.0f}%" if TOTALS["raw"] else None)
    col4.metric("Languages", "en / hi / hinglish")

    # --- Why competitor data? ---
    with st.expander(
        "❓ Why did we collect competitor data if the project is about Zepto?",
        expanded=True,
    ):
        st.markdown(f"""
The project is about Zepto — **{zepto_n:,} of {TOTALS['raw']:,} snippets ({100*zepto_n/max(TOTALS['raw'],1):.0f}%) are Zepto**.
The other **{comp_n:,} snippets ({100*comp_n/max(TOTALS['raw'],1):.0f}%) are from three competitors** (Blinkit, BigBasket, Swiggy Instamart)
and serve a **specific analytical purpose**, not sample-padding:

1. **Cross-source validation (a hard quality check).** For an insight to be tagged
   `confirmed` in the "Quality validated" tab, its supporting evidence must span
   at least **2 sources AND 2 brands**. Without competitor data, this check is
   meaningless and every Zepto-only insight would degrade to `exploratory`.

2. **Zepto-specific vs industry-wide separation.** If users complain about the
   same thing on all four apps, it's an industry-level friction — not a Zepto
   opportunity. Competitor data lets us tell those apart.

3. **Real user comparison behavior — key for the exploration goal.** Users
   *naturally* express category-exploration mental models while comparing brands:
   "I use Zepto for groceries but Blinkit for produce" — this comparison IS the
   mental model we're trying to surface. It only appears when both brands are named.

**What competitor data materially contributed:**
- v2.1 #1 (Repeat-Order Habit Loops) — built on cross-brand price-comparison quotes.
- v2.1 #3 (Zepto Cafe vs Blinkit Bistro) — Zepto's real-world category-expansion
  attempt, only visible with competitor context.
- Reasoning-layer #3 (Trust is category-specific) — validated across all four brands.

Competitor data is a **control group**, deliberately smaller than the Zepto
primary set. It is *not* used to make claims about the competitors themselves.
""")

    if not raw.empty:
        # Source × Brand matrix
        st.markdown("#### Breakdown by source × brand")
        pivot = raw.pivot_table(index="source", columns="brand", values="n", fill_value=0)
        pivot["TOTAL"] = pivot.sum(axis=1)
        pivot.loc["TOTAL"] = pivot.sum(axis=0)
        st.dataframe(pivot, use_container_width=True)

        # Three charts: source, brand pie, primary-vs-control split
        c1, c2, c3 = st.columns(3)
        with c1:
            by_source = raw.groupby("source", as_index=False)["n"].sum().sort_values("n", ascending=False)
            st.plotly_chart(
                px.pie(by_source, values="n", names="source", title="By source",
                       hole=0.4),
                use_container_width=True,
            )
        with c2:
            by_brand = raw.groupby("brand", as_index=False)["n"].sum().sort_values("n", ascending=False)
            st.plotly_chart(
                px.pie(by_brand, values="n", names="brand", title="By brand",
                       hole=0.4),
                use_container_width=True,
            )
        with c3:
            role = pd.DataFrame([
                {"role": "Zepto (primary)", "n": zepto_n},
                {"role": "Competitors (control)", "n": comp_n},
            ])
            st.plotly_chart(
                px.pie(role, values="n", names="role", title="Primary vs control",
                       hole=0.4,
                       color_discrete_map={"Zepto (primary)": "#8B33F7",
                                           "Competitors (control)": "#B4B4C1"}),
                use_container_width=True,
            )

    st.markdown("#### How each source was collected")
    st.markdown("""
**📱 Play Store** *(direct library — Google Play internal API)*
- Zepto (`com.zeptoconsumerapp`), Blinkit (`com.grofers.customerapp`), BigBasket (`com.bigbasket.mobileapp`), Swiggy Instamart (`in.swiggy.android`).
- India, English, up to 5,000 newest reviews per app.

**💬 Reddit** *(Apify actor `oAuCIx3ItNrs2okjQ`)*
- 30 subreddits — r/Zepto, r/india, r/mumbai, r/bangalore, r/AskIndia, r/IndianStartups, r/IndianFood, r/IndiaInvestments, plus 22 more India-focused subs.
- 25 deep search terms — `quick commerce india`, `10 minute delivery`, `kirana vs online`, `grocery habits`, `dhaba vs zomato`, etc.

**📺 YouTube comments** *(youtube-comment-downloader — free)*
- 36 curated videos: Zepto Cafe launch, WTF podcast with Aadit Palicha, comparison reviews, business-model breakdowns, grocery hauls, founder interviews.
- Up to 300 popular comments per video.

**📰 News + business media** *(BeautifulSoup + Google News RSS)*
- 12 Zepto-focused queries fed through Google News RSS.
- Real article URLs decoded and scraped — Inc42, YourStory, LiveMint, Business Standard, Moneycontrol, NDTV Profit, Hindu BusinessLine, and 15 more preferred publishers.

**❌ Skipped sources (documented gaps)**
- Twitter/X and App Store scrapers gated behind Apify paid plans.
- Apple App Store direct endpoint is broken (Apple gated it in 2024).
""")


# ==================================================================
# 3) FILTERED DATA
# ==================================================================
with tab_filter:
    st.markdown("### Filtered data — 8-layer sequential filtration")
    st.caption(
        "Per the PDF methodology, every snippet flows through eight sequential filters. "
        "Each filter is designed to remove a specific kind of noise or downweight low-value signal."
    )

    sq = load_snippet_quality()
    if sq.empty:
        st.warning("Filtration hasn't run yet.")
    else:
        # Funnel counts
        raw = TOTALS["raw"]
        lang_counted = int(sq["lang"].notna().sum())
        spam = int(sq["is_spam"].fillna(False).sum())
        irrelevant = int(((sq["is_spam"] == False) & (sq["is_relevant"] == False)).sum())
        dupes = int(sq["dup_of"].notna().sum())
        v2_keepers = int(((sq["is_spam"] == False) & (sq["is_relevant"] == True) & (sq["dup_of"].isna())).sum())
        v2_1_keepers = int(((sq["is_spam"] == False) & (sq["dup_of"].isna()) &
                            (sq["is_expansion_relevant"] == True)).sum())

        # Funnel chart
        st.markdown("#### Filtration funnel")
        fig = go.Figure(go.Funnel(
            y=[
                "Raw scraped",
                "Language detected + normalized",
                "Not spam",
                "Relevant to shopping behavior",
                "Unique (not near-dup)",
                "v2.1 — expansion-behavior only",
            ],
            x=[
                raw,
                lang_counted,
                lang_counted - spam,
                lang_counted - spam - irrelevant,
                v2_keepers,
                v2_1_keepers,
            ],
            textinfo="value+percent initial",
        ))
        fig.update_layout(margin=dict(l=0, r=0, t=0, b=0), height=380)
        st.plotly_chart(fig, use_container_width=True)

        # Per-layer explanation table
        st.markdown("#### Per-layer detail")
        rows = [
            ("1. Language & Normalization",
             "FastText language detection + brand-name canonicalization (Blinkit variants → 'blinkit', etc.). Non-en/hi rows kept but flagged.",
             f"{lang_counted:,} rows classified"),
            ("2. Spam & Bot Detection",
             "Regex fast-path (Telegram handles, coupon codes, generic 'good app 👍') + DeepSeek LLM classifier on borderline rows.",
             f"❌ {spam:,} dropped ({100*spam/raw:.1f}%)"),
            ("3. Relevance Filter",
             "Legacy keyword+brand check + DeepSeek verifies each snippet is really about *shopping behavior, discovery, habits* — not just operational bugs.",
             f"❌ {irrelevant:,} more dropped as irrelevant"),
            ("4. Semantic Deduplication",
             "Cosine similarity ≥ 0.95 against every previous embedding (OpenAI text-embedding-3-small). Earliest snippet kept.",
             f"❌ {dupes:,} near-duplicates flagged ({100*dupes/raw:.1f}%)"),
            ("5. Behaviour Filter",
             "7 boolean flags per snippet: describes routine, repeat_purchase, exploration, trust, hesitation, price_sensitivity, decision_process.",
             "Weighting input"),
            ("6. Specificity Filter",
             "1–5 rating on how detailed the review is. 'Good app' scores 1; a specific product complaint scores 5.",
             "Weighting input"),
            ("7. Information Value Score",
             "Composite: 0.30·specificity + 0.20·novelty + 0.20·behavioural + 0.15·clarity + 0.15·actionability.",
             f"{int(sq['info_value_score'].notna().sum()):,} rows scored"),
            ("8. Temporal / Regional weighting",
             "Recency decay (half-life 180d) + city-inferred where possible. Applied at theme-scoring, not filtration.",
             "Downstream weight"),
        ]
        st.dataframe(
            pd.DataFrame(rows, columns=["Layer", "What it does", "Result"]),
            hide_index=True, use_container_width=True,
        )

        # Two keeper universes
        st.markdown("#### Two candidate universes")
        c1, c2 = st.columns(2)
        c1.metric("v2 keepers (broad relevance)", f"{v2_keepers:,}",
                  help="All snippets that survive filters 1–4. Used for the general v2 pipeline run.")
        c2.metric("v2.1 keepers (expansion-only)", f"{v2_1_keepers:,}",
                  help="Restricted to snippets whose behaviour_flags describe exploration / hesitation / decision-process. Directly on the MAC-per-new-category goal.")


# ==================================================================
# 4) THEMES GENERATED
# ==================================================================
with tab_themes:
    st.markdown("### Themes generated — evolving taxonomy")
    st.caption(
        "Themes emerge dynamically (seed → grow → consolidate) rather than being pre-defined. "
        "12 seed themes are generated from a 200-review sample; new snippets that don't match are "
        "pooled and periodically clustered into new themes. Every 500 assignments, near-duplicate themes are merged."
    )

    tax_v = st.radio(
        "Taxonomy version",
        options=[1, 2],
        format_func=lambda v: {1: "v2 (all keepers)", 2: "v2.1 (expansion-only)"}[v],
        horizontal=True,
    )

    th = load_themes_and_members(tax_v)
    if th.empty:
        st.info("No themes yet at this version.")
    else:
        n_parents = int(th["parent_id"].isna().sum())
        n_leaves = int(th["parent_id"].notna().sum())
        promoted = int((th["status"] == "promoted").sum())

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total themes", len(th))
        c2.metric("Parent buckets", n_parents)
        c3.metric("Leaf themes", n_leaves)
        c4.metric("Emergent (DBSCAN-promoted)", promoted)

        # Bar chart: themes by member count
        st.markdown("#### Themes by supporting-review count")
        leaves = th[th["members"] > 0].sort_values("members", ascending=True).tail(20)
        if not leaves.empty:
            fig = px.bar(
                leaves, x="members", y="name", color="parent_name",
                orientation="h",
                labels={"members": "# supporting reviews", "name": "theme"},
                height=500,
            )
            fig.update_layout(yaxis=dict(tickmode="linear"), margin=dict(l=0, r=0, t=10, b=0))
            st.plotly_chart(fig, use_container_width=True)

        # Hierarchical view
        st.markdown("#### Hierarchy — parent buckets → leaf themes")
        parents = th[th["parent_id"].isna()]
        for _, p in parents.iterrows():
            children = th[th["parent_id"] == p["id"]].sort_values("members", ascending=False)
            total_members = int(children["members"].sum())
            with st.expander(f"📂 **{p['name']}**  ·  {len(children)} themes  ·  {total_members} supporting reviews", expanded=(total_members > 20)):
                if p["definition"]:
                    st.caption(p["definition"])
                for _, c in children.iterrows():
                    badge = "🌱 seed" if c["status"] == "seed" else "✨ promoted"
                    st.markdown(f"- **{c['name']}**  ·  {c['members']} reviews  ·  {badge}")
                    if c["definition"]:
                        st.caption(f"  _{c['definition']}_")

        # Full table for review
        with st.expander("All themes as a table"):
            st.dataframe(
                th[["id", "name", "status", "parent_name", "members", "definition"]],
                hide_index=True, use_container_width=True,
            )


# ==================================================================
# 5) INSIGHTS GENERATED
# ==================================================================
with tab_insights:
    st.markdown("### Insights generated")
    st.caption(
        "For every theme with ≥ 5 supporting reviews, DeepSeek generated a hypothesis with cited "
        "evidence, a suggested 2-week experiment, and an interview probe. Reasoning-layer hypotheses "
        "were generated separately by GPT-4.1 reasoning across the entire corpus."
    )

    hyps = load_corpus_hypotheses()
    v2 = load_insights_v2()
    v1 = load_v1_cards()
    v3 = load_v3_cards()

    total = len(hyps) + len(v2) + len(v1) + len(v3)
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Total insights", total)
    c2.metric("Reasoning-layer", len(hyps))
    c3.metric("v2 (broad)", int((v2["taxonomy_version"] == 1).sum()) if not v2.empty else 0)
    c4.metric("v2.1 (expansion)", int((v2["taxonomy_version"] == 2).sum()) if not v2.empty else 0)
    c5.metric("v1 + v3 baselines", len(v1) + len(v3))

    method = st.selectbox(
        "Show insights from",
        options=["v2.1 (expansion-focused)", "v2 (broad)",
                 "Reasoning layer", "v3 (adversarial baseline)", "v1 (simple baseline)"],
    )

    if method.startswith("Reasoning"):
        if hyps.empty:
            st.info("No reasoning-layer hypotheses.")
        else:
            for _, h in hyps.iterrows():
                emoji = {"high": "🟢", "medium": "🟡", "low": "🔴"}.get(h["confidence"], "⚪")
                with st.container(border=True):
                    st.subheader(f"#{h['rank']}  ·  {h['title']}  ·  {emoji} {h['confidence']}")
                    st.markdown(f"**Claim:** {h['claim']}")
                    st.markdown(f"**Reasoning:** {h['reasoning']}")
                    st.markdown(f"**Implication:** {h['implication_for_zepto']}")
                    st.markdown(f"**Interview probe:** _{h['interview_probe']}_")

    elif method.startswith("v2 ") or method.startswith("v2.1"):
        version = 2 if method.startswith("v2.1") else 1
        sub = v2[v2["taxonomy_version"] == version] if not v2.empty else pd.DataFrame()
        if sub.empty:
            st.info("No insights at this version.")
        else:
            for _, r in sub.iterrows():
                badge = {"confirmed": "🟢 confirmed", "exploratory": "🟡 exploratory",
                         "shelved": "🔴 shelved", "revising": "🟠 revising"}.get(
                         r["validation_status"], r["validation_status"])
                with st.container(border=True):
                    st.subheader(f"{r['theme']}  ·  conf {r['confidence']:.0f}  ·  {badge}")
                    st.markdown(f"**Hypothesis:** {r['hypothesis']}")
                    st.write(r["detailed"])
                    if r["suggested_experiment"]:
                        st.markdown(f"**Experiment:** {r['suggested_experiment']}")
                    if r["part_2_probe"]:
                        st.markdown(f"**Interview probe:** _{r['part_2_probe']}_")

    elif method.startswith("v3"):
        if v3.empty:
            st.info("No v3 cards.")
        else:
            for _, r in v3.iterrows():
                emoji = {"high": "🟢", "medium": "🟡", "low": "🔴"}.get(r["confidence_in_hypothesis"], "⚪")
                with st.container(border=True):
                    st.subheader(f"{r['title']}  ·  score {r['confidence']:.0f}  ·  {emoji} {r['confidence_in_hypothesis']}")
                    st.markdown(f"**Hypothesis:** {r['hypothesis']}")
                    st.write(r["detailed"])
                    st.markdown(f"**Suggested experiment:** {r['suggested_experiment']}")

    else:  # v1
        if v1.empty:
            st.info("No v1 cards.")
        else:
            for _, r in v1.iterrows():
                with st.container(border=True):
                    st.subheader(f"{r['title']}  ·  confidence {r['confidence']:.0f}")
                    st.markdown(f"**{r['one_line']}**")
                    st.write(r["detailed"])
                    st.markdown(f"**Suggested experiment:** {r['suggested_experiment']}")


# ==================================================================
# 6) QUALITY VALIDATED
# ==================================================================
with tab_quality:
    st.markdown("### Insight quality — multi-layer validation")
    st.caption(
        "Every v2 insight was scored on 5 automated validation layers plus a separate LLM Critic Agent "
        "(different model from the generator) that reviewed each insight against retrieved counter-evidence. "
        "Behavioural + Business-experiment validation are honestly flagged as `not_evaluated` (require internal analytics)."
    )

    v2 = load_insights_v2()
    if v2.empty:
        st.warning("No v2 insights yet.")
    else:
        # Explain the checks
        st.markdown("#### The five automated checks")
        checks = pd.DataFrame(
            [
                ("Evidence", "≥ 5 supporting review IDs must be cited by the LLM.",
                 "insights below threshold shelved"),
                ("Cross-source", "Evidence must span ≥ 2 sources AND ≥ 2 brands.",
                 "confirmed only if diverse enough"),
                ("Statistical", "Theme must have ≥ 20 unique authors AND ≥ 0.5% of corpus.",
                 "prevents fringe hypotheses"),
                ("Cluster quality", "Theme intra-cluster mean cosine ≥ 0.70.",
                 "signal is coherent"),
                ("LLM Critic Agent", "GPT-4.1 reviews DeepSeek's output + 10 retrieved counter-quotes.",
                 "pass / revise / reject verdict"),
            ],
            columns=["Layer", "Rule", "Enforcement"],
        )
        st.dataframe(checks, hide_index=True, use_container_width=True)

        # Compute validation matrix
        rows = []
        for _, r in v2.iterrows():
            bd = _parse_json(r["confidence_breakdown"]) or {}
            rows.append({
                "id": r["id"],
                "theme": r["theme"],
                "confidence": r["confidence"],
                "status": r["validation_status"],
                "evidence_pass": "✓" if bd.get("evidence_pass") else "✗",
                "cross_source_pass": "✓" if bd.get("cross_source_pass") else "✗",
                "n_sources": bd.get("evidence_source_count", "-"),
                "n_brands": bd.get("evidence_brand_count", "-"),
                "statistical_pass": "✓" if bd.get("statistical_pass") else "✗",
                "unique_authors": bd.get("unique_authors", "-"),
                "cluster_quality_pass": "✓" if bd.get("cluster_quality_pass") else "✗",
                "intra_cosine": bd.get("intra_cluster_sim", "-"),
                "critic_verdict": bd.get("critic_verdict") or r["critic_verdict"] or "-",
            })
        vm = pd.DataFrame(rows)

        # Aggregate: how many usable
        n_total = len(vm)
        n_confirmed = int((vm["status"] == "confirmed").sum())
        n_exploratory = int((vm["status"] == "exploratory").sum())
        n_shelved = int((vm["status"] == "shelved").sum())
        n_critic_pass = int((vm["critic_verdict"] == "pass").sum())
        n_critic_revise = int((vm["critic_verdict"] == "revise").sum())
        n_critic_reject = int((vm["critic_verdict"] == "reject").sum())

        st.markdown("#### Final ranking of insights")
        st.dataframe(
            vm[["id", "theme", "confidence", "status", "evidence_pass", "cross_source_pass",
                "n_sources", "n_brands", "statistical_pass", "unique_authors",
                "cluster_quality_pass", "intra_cosine", "critic_verdict"]],
            hide_index=True, use_container_width=True,
        )

        # Usability summary
        st.markdown("#### Usability summary")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total insights", n_total)
        c2.metric("🟢 Confirmed (all hard checks + critic PASS)", n_confirmed)
        c3.metric("🟡 Exploratory (some checks miss OR critic REVISE)", n_exploratory)
        c4.metric("🔴 Shelved (fails hard OR critic REJECT)", n_shelved)

        st.markdown("#### LLM Critic verdicts")
        c1, c2, c3 = st.columns(3)
        c1.metric("PASS", n_critic_pass)
        c2.metric("REVISE", n_critic_revise)
        c3.metric("REJECT", n_critic_reject)

        # What's honestly missing
        st.markdown("#### Honest gaps in validation")
        st.warning(
            "**Behavioural validation** (recommendation CTR, category visits, repeat purchases) and "
            "**Business & Experiment validation** (A/B test uplift) require Zepto internal analytics we "
            "don't have access to. These layers are flagged `not_evaluated` and `deferred_to_part_4` "
            "in every insight's confidence breakdown — not silently skipped."
        )

        # How to use
        st.markdown("#### How to use these insights")
        if n_confirmed > 0:
            st.success(f"**{n_confirmed} insights** cleared all hard checks. Use them as primary drivers.")
        if n_exploratory > 0:
            st.info(
                f"**{n_exploratory} insights** are exploratory — the signal is real but needs one more "
                "layer of evidence. **These are the ideal Part 2 interview subjects** — the pipeline "
                "already generated open-ended interview probes for each."
            )
        if n_shelved > 0:
            st.error(
                f"**{n_shelved} insights** were shelved by the critic — treated as noise. Kept in the "
                "table for transparency, but should not drive product decisions."
            )
