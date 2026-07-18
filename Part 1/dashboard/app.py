"""Streamlit dashboard — the workflow deliverable.

Run locally (with Docker Postgres up):
    streamlit run dashboard/app.py

Deploy to Streamlit Community Cloud:
    Point at repo path dashboard/app.py — it auto-detects that Postgres is
    unreachable and falls back to DuckDB reading the Parquet snapshots in
    Part 1/demo_data/.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

# Allow `streamlit run dashboard/app.py` from the project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import plotly.express as px
import streamlit as st

from dashboard.data import has_table, is_demo_mode, query

st.set_page_config(page_title="Zepto Discovery Engine", layout="wide")


@st.cache_data(ttl=300)
def load_insight_cards() -> pd.DataFrame:
    return query(
        "SELECT id, title, one_line, detailed, persona_most_affected, primary_barrier, "
        "       suggested_experiment, confidence, confidence_breakdown, source_counts, "
        "       unique_authors, created_at "
        "FROM insight_cards ORDER BY confidence DESC"
    )


@st.cache_data(ttl=300)
def load_extracted() -> pd.DataFrame:
    return query(
        "SELECT id, intent, themes, user_persona, category_currently_buying, "
        "       category_avoiding, barrier_summary, emotional_tone, actionable_quote, "
        "       source, text, posted_at "
        "FROM extracted_insights "
        "WHERE intent != 'Irrelevant' "
        "ORDER BY posted_at DESC NULLS LAST LIMIT 5000"
    )


@st.cache_data(ttl=300)
def counts() -> dict:
    try:
        r = query(
            "SELECT COUNT(*) AS raw FROM raw_counts"
        )
        # In demo mode, raw_totals is a single-row precomputed table
        if has_table("raw_totals"):
            df = query("SELECT * FROM raw_totals LIMIT 1")
            if not df.empty:
                row = df.iloc[0]
                return {
                    "raw":       int(row.get("raw", 0)),
                    "filtered":  int(row.get("filtered", 0)),
                    "extracted": int(row.get("extracted", 0)),
                    "cards":     int(row.get("cards", 0)),
                }
    except Exception:
        pass
    # Live mode fallback
    return {
        "raw":       int(query("SELECT COUNT(*) AS n FROM raw_snippets")["n"].iloc[0]) if has_table("raw_snippets") else 0,
        "filtered":  int(query("SELECT COUNT(*) AS n FROM filtered_snippets")["n"].iloc[0]) if has_table("filtered_snippets") else 0,
        "extracted": int(query("SELECT COUNT(*) AS n FROM extracted_insights")["n"].iloc[0]) if has_table("extracted_insights") else 0,
        "cards":     int(query("SELECT COUNT(*) AS n FROM insight_cards")["n"].iloc[0]) if has_table("insight_cards") else 0,
    }


st.title("Zepto AI-Powered Discovery Engine")
st.caption("Systematically surfacing *why* Zepto users stay in habit loops and rarely try new categories.")
if is_demo_mode():
    st.info("📦 **Demo mode** — reading pre-computed snapshots. See the [source repo](https://github.com/rushilv698/Zepto---Nextleap-Grad-project-1-) for the live pipeline.")

with st.sidebar:
    st.header("Pipeline stats")
    try:
        c = counts()
        st.metric("Raw snippets",       f"{c['raw']:,}")
        st.metric("Filtered relevant",  f"{c['filtered']:,}")
        st.metric("Extracted insights", f"{c['extracted']:,}")
        st.metric("Insight cards",      f"{c['cards']:,}")
    except Exception as e:
        st.error(f"Stats unavailable: {e}")

tab_v2, tab0, tab1, tab2, tab_v3, tab3, tab4 = st.tabs(
    ["v2 Themes + Insights (validated)", "Corpus hypotheses (reasoning)",
     "Strategic Q&A", "Insight cards (v1)", "Insight cards (v3, adversarial)",
     "Trends", "Raw explorer"]
)

# ------------- v2 THEMES + VALIDATED INSIGHTS -----------------
with tab_v2:
    st.markdown("### v2 pipeline — evolving taxonomy + multi-layer validated insights")
    st.caption("Per PDF methodology. Themes emerge dynamically (seed → grow → consolidate); "
               "each insight is scored on 5 automated validation layers + LLM critic; behavioural "
               "and business/experiment validation are honestly marked as gaps.")
    tax_v = st.radio(
        "Taxonomy version",
        options=[1, 2],
        format_func=lambda v: {1: "v2 (all keepers, 910 rows)", 2: "v2.1 (expansion-only, 546 rows)"}[v],
        horizontal=True,
        help="v2.1 is the focused rerun using ONLY snippets whose behaviour_flags describe "
             "exploration / hesitation / decision-process — directly on the MAC-per-new-category goal.",
    )
    try:
        if not has_table("insights_v2"):
            st.info("v2 pipeline not yet run.")
        else:
            # Corpus health
            try:
                q = query("""
                    SELECT COALESCE(CAST(is_spam AS VARCHAR), 'null') AS is_spam,
                           COALESCE(CAST(is_relevant AS VARCHAR), 'null') AS is_relevant,
                           CASE WHEN dup_of IS NOT NULL THEN 'dup' ELSE 'unique' END AS dedup,
                           COUNT(*) AS n
                    FROM snippet_quality GROUP BY 1,2,3 ORDER BY 4 DESC
                """)
                if not q.empty:
                    st.subheader("Filtration funnel")
                    st.dataframe(q, hide_index=True)
            except Exception:
                pass

            # Theme taxonomy summary
            th = query("""
                SELECT t.id, t.name, t.definition, t.status, t.parent_id,
                       (SELECT name FROM themes p WHERE p.id = t.parent_id) AS parent_name,
                       (SELECT COUNT(*) FROM review_themes rt WHERE rt.theme_id = t.id
                          AND rt.taxonomy_version = :v) AS members
                FROM themes t
                WHERE t.merged_into IS NULL AND t.taxonomy_version = :v
                ORDER BY parent_id NULLS FIRST, members DESC
            """, params={"v": int(tax_v)})
            if not th.empty:
                st.subheader(f"Taxonomy v{tax_v} — {len(th)} themes")
                st.dataframe(th[["id", "name", "status", "parent_name", "members", "definition"]],
                             hide_index=True, use_container_width=True)
            else:
                st.info("No themes at this version yet.")

            # Insights
            ins = query("""
                SELECT id, theme_id, theme, hypothesis, one_line, detailed,
                       suggested_experiment, part_2_probe,
                       confidence, validation_status, critic_verdict, critic_notes,
                       confidence_breakdown
                FROM insights_v2
                WHERE taxonomy_version = :v
                ORDER BY confidence DESC NULLS LAST
            """, params={"v": int(tax_v)})
            if ins.empty:
                st.info("No insights at this version.")
            else:
                st.subheader(f"Insights — {len(ins)} generated")
                statuses = ["(any)"] + sorted(ins["validation_status"].dropna().unique().tolist())
                verdicts = ["(any)"] + sorted(ins["critic_verdict"].dropna().unique().tolist())
                col1, col2, col3 = st.columns(3)
                with col1: st_pick = st.selectbox("Status", statuses, key=f"v2_status_{tax_v}")
                with col2: v_pick = st.selectbox("Critic verdict", verdicts, key=f"v2_verdict_{tax_v}")
                with col3: min_c = st.slider("Min confidence", 0, 100, 30, key=f"v2_conf_{tax_v}")
                f = ins[ins["confidence"].fillna(0) >= min_c]
                if st_pick != "(any)": f = f[f["validation_status"] == st_pick]
                if v_pick != "(any)": f = f[f["critic_verdict"] == v_pick]
                for _, r in f.iterrows():
                    badge = {"confirmed": "🟢 confirmed", "exploratory": "🟡 exploratory",
                             "shelved": "🔴 shelved", "revising": "🟠 revising"}.get(r["validation_status"], r["validation_status"])
                    with st.container(border=True):
                        st.subheader(f"{r['theme']}  ·  conf {r['confidence']:.0f}  ·  {badge}")
                        st.markdown(f"**Hypothesis:** {r['hypothesis']}")
                        if r["one_line"]:
                            st.markdown(f"_{r['one_line']}_")
                        st.write(r["detailed"])
                        if r["suggested_experiment"]:
                            st.markdown(f"**Experiment:** {r['suggested_experiment']}")
                        if r["part_2_probe"]:
                            st.markdown(f"**Interview probe:** {r['part_2_probe']}")
                        if r["critic_notes"]:
                            with st.expander(f"LLM critic verdict: {r['critic_verdict']}"):
                                st.write(r["critic_notes"])
                        bd = r["confidence_breakdown"]
                        if bd:
                            try:
                                b = bd if isinstance(bd, dict) else json.loads(bd)
                                with st.expander("Validation breakdown"):
                                    st.json(b)
                            except Exception:
                                pass
    except Exception as e:
        st.error(f"v2 tab failed: {e}")


# ------------- Corpus hypotheses (reasoning layer) --------------
with tab0:
    st.markdown("### Reasoning layer output — hypotheses inferred *across the whole corpus*")
    st.caption("Different in kind from insight cards: this asked GPT-4.1 to reason about the corpus as a whole, including inferring signal from what's ABSENT.")
    try:
        top_df = query(
            "SELECT DISTINCT top_line_read, what_this_corpus_cannot_answer, recommended_next_data_collection "
            "FROM corpus_hypotheses LIMIT 1"
        )
        top = None if top_df.empty else top_df.iloc[0]
        hyps = query(
            "SELECT rank, title, claim, reasoning, grounded_in, "
            "counter_evidence_that_would_disprove, confidence, novelty, "
            "implication_for_zepto, interview_probe "
            "FROM corpus_hypotheses ORDER BY rank"
        )
        if top is not None:
            st.info(f"**Top-line read:** {top['top_line_read']}")
        if hyps.empty:
            st.warning("No corpus hypotheses yet.")
        else:
            for _, h in hyps.iterrows():
                emoji = {"high": "🟢", "medium": "🟡", "low": "🔴"}.get(h["confidence"], "⚪")
                nov_tag = "✨ non-obvious" if h["novelty"] == "non_obvious" else "obvious"
                with st.container(border=True):
                    st.subheader(f"#{h['rank']}  ·  {h['title']}  ·  {emoji} {h['confidence']}  ·  {nov_tag}")
                    st.markdown(f"**Claim:** {h['claim']}")
                    st.markdown(f"**Reasoning:** {h['reasoning']}")
                    gi = h["grounded_in"]
                    if gi:
                        try:
                            items = gi if isinstance(gi, list) else json.loads(gi)
                            st.markdown("**Grounded in:** " + " · ".join(f"`{x}`" for x in items))
                        except Exception:
                            pass
                    st.markdown(f"**Counter-evidence that would disprove:** {h['counter_evidence_that_would_disprove']}")
                    st.markdown(f"**Implication for Zepto:** {h['implication_for_zepto']}")
                    st.markdown(f"**Interview probe:** _{h['interview_probe']}_")
        if top is not None:
            with st.expander("Honest limits of this corpus"):
                st.markdown(f"**Can't answer:** {top['what_this_corpus_cannot_answer']}")
                st.markdown(f"**Recommended next data:** {top['recommended_next_data_collection']}")
    except Exception as e:
        st.error(f"Failed to load hypotheses: {e}")


# ------------- Strategic Q&A -----------------
STRATEGIC_QS = [
    ("Why do users repeatedly buy from the same categories?",
     lambda df: df[df["intent"] == "Repeat_Purchase_Habit"]),
    ("What prevents users from exploring new categories?",
     lambda df: df[df["intent"] == "Exploration_Blocker"]),
    ("What role do habits play in shopping behavior?",
     lambda df: df[df["themes"].apply(lambda t: t is not None and "habit_loop" in (t if isinstance(t, list) else json.loads(t) if isinstance(t, str) and t.startswith('[') else []))]),
    ("What information do users need before trying a new category?",
     lambda df: df[df["themes"].apply(lambda t: t is not None and "information_gap" in (t if isinstance(t, list) else json.loads(t) if isinstance(t, str) and t.startswith('[') else []))]),
    ("What frustrations emerge repeatedly?",
     lambda df: df[df["emotional_tone"].isin(["frustration", "anger"])]),
    ("Which user segments are more likely to experiment?",
     lambda df: df[df["intent"].isin(["Discovery_Request", "Unmet_Need"])]),
    ("What unmet needs emerge consistently across discussions?",
     lambda df: df[df["intent"] == "Unmet_Need"]),
    ("How do users discover products today?",
     lambda df: df[df["themes"].apply(lambda t: t is not None and "discovery_UI" in (t if isinstance(t, list) else json.loads(t) if isinstance(t, str) and t.startswith('[') else []))]),
]

with tab1:
    try:
        ex = load_extracted()
        for q_text, filt in STRATEGIC_QS:
            with st.expander(q_text, expanded=False):
                sub = filt(ex)
                st.write(f"**{len(sub)} snippets matched**")
                if len(sub) == 0:
                    st.info("No data yet.")
                    continue
                theme_counter = Counter()
                for lst in sub["themes"].dropna():
                    if isinstance(lst, str):
                        try:
                            lst = json.loads(lst)
                        except Exception:
                            lst = []
                    if isinstance(lst, list):
                        theme_counter.update(lst)
                if theme_counter:
                    tt = pd.DataFrame(theme_counter.most_common(8), columns=["theme", "count"])
                    st.plotly_chart(px.bar(tt, x="theme", y="count"), use_container_width=True)
                st.markdown("**Sample quotes:**")
                for _, row in sub[sub["actionable_quote"] == True].head(5).iterrows():
                    st.markdown(f"> {row['text'][:400]}\n> — *{row['source']}*")
    except Exception as e:
        st.error(f"Failed to load: {e}")


# ------------- Insight cards v1 ------------------
with tab2:
    try:
        cards = load_insight_cards()
        if cards.empty:
            st.info("No insight cards yet.")
        else:
            personas = ["(any)"] + sorted(cards["persona_most_affected"].dropna().unique().tolist())
            barriers = ["(any)"] + sorted(cards["primary_barrier"].dropna().unique().tolist())
            col1, col2, col3 = st.columns(3)
            with col1: p = st.selectbox("Persona", personas)
            with col2: b = st.selectbox("Primary barrier", barriers)
            with col3: min_conf = st.slider("Min confidence", 0, 100, 50)
            f = cards[cards["confidence"] >= min_conf]
            if p != "(any)": f = f[f["persona_most_affected"] == p]
            if b != "(any)": f = f[f["primary_barrier"] == b]
            st.write(f"**{len(f)} cards**")
            for _, r in f.iterrows():
                with st.container(border=True):
                    st.subheader(f"{r['title']}  ·  confidence {r['confidence']:.0f}")
                    st.markdown(f"**{r['one_line']}**")
                    st.write(r["detailed"])
                    cols = st.columns(3)
                    cols[0].markdown(f"**Persona**: {r['persona_most_affected']}")
                    cols[1].markdown(f"**Barrier**: {r['primary_barrier']}")
                    cols[2].markdown(f"**Unique authors**: {r['unique_authors']}")
                    if r["source_counts"]:
                        try:
                            sc = r["source_counts"] if isinstance(r["source_counts"], dict) else json.loads(r["source_counts"])
                            st.caption(" · ".join(f"{k}: {v}" for k, v in sc.items()))
                        except Exception:
                            pass
                    st.markdown(f"**Suggested experiment:** {r['suggested_experiment']}")
    except Exception as e:
        st.error(f"Failed to load: {e}")


# ------------- Insight cards v3 (adversarial) --------------
with tab_v3:
    st.caption("Same clustering + hypothesis + counter-evidence + Part-2 interview probes. Use these cards to write your Part 2 interview scripts.")
    try:
        v3 = query(
            "SELECT id, title, hypothesis, detailed, persona_most_affected, primary_barrier, "
            "supporting_evidence, counter_evidence_check, confidence_in_hypothesis, "
            "suggested_experiment, part_2_interview_prompts, confidence, source_counts, "
            "brand_counts, discovery_breakdown, unique_authors "
            "FROM insight_cards_v3 ORDER BY confidence DESC"
        )
        if v3.empty:
            st.warning("No v3 cards.")
        else:
            conf_h = ["(any)"] + sorted(v3["confidence_in_hypothesis"].dropna().unique().tolist())
            personas = ["(any)"] + sorted(v3["persona_most_affected"].dropna().unique().tolist())
            barriers = ["(any)"] + sorted(v3["primary_barrier"].dropna().unique().tolist())
            col1, col2, col3, col4 = st.columns(4)
            with col1: c_h = st.selectbox("Model confidence", conf_h, key="v3_ch")
            with col2: p3 = st.selectbox("Persona", personas, key="v3_p")
            with col3: b3 = st.selectbox("Barrier", barriers, key="v3_b")
            with col4: min_conf3 = st.slider("Min confidence score", 0, 100, 50, key="v3_mc")
            f = v3[v3["confidence"] >= min_conf3]
            if c_h != "(any)": f = f[f["confidence_in_hypothesis"] == c_h]
            if p3 != "(any)": f = f[f["persona_most_affected"] == p3]
            if b3 != "(any)": f = f[f["primary_barrier"] == b3]
            st.write(f"**{len(f)} cards**")
            for _, r in f.iterrows():
                emoji = {"high": "🟢", "medium": "🟡", "low": "🔴"}.get(r["confidence_in_hypothesis"], "⚪")
                with st.container(border=True):
                    st.subheader(f"{r['title']}  ·  score {r['confidence']:.0f}  ·  {emoji} {r['confidence_in_hypothesis']}")
                    st.markdown(f"**Hypothesis:** {r['hypothesis']}")
                    st.write(r["detailed"])
                    cols = st.columns(3)
                    cols[0].markdown(f"**Persona**: {r['persona_most_affected']}")
                    cols[1].markdown(f"**Barrier**: {r['primary_barrier']}")
                    cols[2].markdown(f"**Unique authors**: {r['unique_authors']}")
                    if r["brand_counts"]:
                        try:
                            bc = r["brand_counts"] if isinstance(r["brand_counts"], dict) else json.loads(r["brand_counts"])
                            st.caption("Brands: " + " · ".join(f"{k}: {v}" for k, v in bc.items()))
                        except Exception:
                            pass
                    with st.expander("Supporting evidence"):
                        st.write(r["supporting_evidence"])
                    with st.expander("Counter-evidence check"):
                        st.write(r["counter_evidence_check"])
                    st.markdown(f"**Suggested experiment:** {r['suggested_experiment']}")
                    prompts = r["part_2_interview_prompts"]
                    if prompts:
                        try:
                            qs = prompts if isinstance(prompts, list) else json.loads(prompts)
                            st.markdown("**Part-2 interview probes:**")
                            for q in qs:
                                st.markdown(f"- _{q}_")
                        except Exception:
                            pass
    except Exception as e:
        st.error(f"Failed to load v3 cards: {e}")


# ------------- Trends -------------------------
with tab3:
    try:
        ex = load_extracted()
        if ex.empty or ex["posted_at"].dropna().empty:
            st.info("Not enough dated data yet.")
        else:
            ex["day"] = pd.to_datetime(ex["posted_at"]).dt.date
            counts_by_day_intent = ex.groupby(["day", "intent"]).size().reset_index(name="n")
            st.plotly_chart(
                px.line(counts_by_day_intent, x="day", y="n", color="intent",
                        title="Signal volume by intent, over time"),
                use_container_width=True,
            )
            # Explode themes for time-series
            ex_copy = ex.copy()
            ex_copy["themes_list"] = ex_copy["themes"].apply(
                lambda t: t if isinstance(t, list) else (json.loads(t) if isinstance(t, str) and t.startswith('[') else [])
            )
            explode = ex_copy.explode("themes_list")
            explode = explode[explode["themes_list"].notna()]
            if not explode.empty:
                byday = explode.groupby(["day", "themes_list"]).size().reset_index(name="n")
                top = explode["themes_list"].value_counts().head(5).index.tolist()
                byday = byday[byday["themes_list"].isin(top)]
                st.plotly_chart(
                    px.line(byday, x="day", y="n", color="themes_list", title="Top 5 barriers over time"),
                    use_container_width=True,
                )
    except Exception as e:
        st.error(f"Failed to load: {e}")


# ------------- Raw explorer -------------------
with tab4:
    try:
        ex = load_extracted()
        q_str = st.text_input("Search text (substring)")
        source_opts = ["(all)"] + sorted(ex["source"].dropna().unique().tolist())
        s = st.selectbox("Source", source_opts)
        f = ex
        if q_str: f = f[f["text"].str.contains(q_str, case=False, na=False)]
        if s != "(all)": f = f[f["source"] == s]
        st.write(f"**{len(f)} rows**")
        st.dataframe(
            f[["source", "posted_at", "intent", "user_persona", "category_currently_buying",
               "category_avoiding", "emotional_tone", "barrier_summary", "text"]].head(500),
            use_container_width=True, height=600,
        )
    except Exception as e:
        st.error(f"Failed to load: {e}")
