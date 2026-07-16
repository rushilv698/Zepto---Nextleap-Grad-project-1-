"""Streamlit dashboard — the workflow deliverable.

Run locally:  streamlit run dashboard/app.py
Deploy:       Streamlit Community Cloud → repo path dashboard/app.py
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter
from pathlib import Path

# Allow `streamlit run dashboard/app.py` from the project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import plotly.express as px
import streamlit as st
from sqlalchemy import text

from pipeline.storage import engine

st.set_page_config(page_title="Zepto Discovery Engine", layout="wide")


@st.cache_data(ttl=300)
def load_insight_cards() -> pd.DataFrame:
    q = text(
        "SELECT id, title, one_line, detailed, persona_most_affected, primary_barrier, "
        "       suggested_experiment, confidence, confidence_breakdown, source_counts, "
        "       unique_authors, created_at "
        "FROM insight_cards ORDER BY confidence DESC"
    )
    with engine().begin() as conn:
        return pd.read_sql(q, conn)


@st.cache_data(ttl=300)
def load_extracted() -> pd.DataFrame:
    q = text(
        "SELECT e.id, e.intent, e.themes, e.user_persona, e.category_currently_buying, "
        "       e.category_avoiding, e.barrier_summary, e.emotional_tone, e.actionable_quote, "
        "       r.source, r.text, r.posted_at "
        "FROM extracted_insights e JOIN raw_snippets r ON r.id = e.snippet_id "
        "WHERE e.intent != 'Irrelevant' ORDER BY r.posted_at DESC NULLS LAST LIMIT 5000"
    )
    with engine().begin() as conn:
        return pd.read_sql(q, conn)


@st.cache_data(ttl=300)
def counts() -> dict:
    with engine().begin() as conn:
        return {
            "raw":       conn.execute(text("SELECT COUNT(*) FROM raw_snippets")).scalar_one(),
            "filtered":  conn.execute(text("SELECT COUNT(*) FROM filtered_snippets")).scalar_one() if _has_table("filtered_snippets") else 0,
            "extracted": conn.execute(text("SELECT COUNT(*) FROM extracted_insights")).scalar_one(),
            "cards":     conn.execute(text("SELECT COUNT(*) FROM insight_cards")).scalar_one(),
        }


def _has_table(name: str) -> bool:
    with engine().begin() as conn:
        return bool(conn.execute(
            text("SELECT 1 FROM information_schema.tables WHERE table_name=:n"),
            {"n": name},
        ).first())


st.title("Zepto AI-Powered Discovery Engine")
st.caption("Systematically surfacing *why* Zepto users stay in habit loops and rarely try new categories.")

with st.sidebar:
    st.header("Pipeline stats")
    try:
        c = counts()
        st.metric("Raw snippets",       f"{c['raw']:,}")
        st.metric("Filtered relevant",  f"{c['filtered']:,}")
        st.metric("Extracted insights", f"{c['extracted']:,}")
        st.metric("Insight cards",      f"{c['cards']:,}")
    except Exception as e:
        st.error(f"DB unavailable: {e}")

tab1, tab2, tab3, tab4 = st.tabs(
    ["Strategic Q&A", "Insight cards", "Trends", "Raw explorer"]
)

# ------------- Strategic Q&A -----------------
STRATEGIC_QS = [
    ("Why do users repeatedly buy from the same categories?",
     lambda df: df[df["intent"] == "Repeat_Purchase_Habit"]),
    ("What prevents users from exploring new categories?",
     lambda df: df[df["intent"] == "Exploration_Blocker"]),
    ("What role do habits play in shopping behavior?",
     lambda df: df[df["themes"].apply(lambda t: "habit_loop" in (t or []))]),
    ("What information do users need before trying a new category?",
     lambda df: df[df["themes"].apply(lambda t: "information_gap" in (t or []))]),
    ("What frustrations emerge repeatedly?",
     lambda df: df[df["emotional_tone"].isin(["frustration", "anger"])]),
    ("Which user segments are more likely to experiment?",
     lambda df: df[df["intent"].isin(["Discovery_Request", "Unmet_Need"])]),
    ("What unmet needs emerge consistently across discussions?",
     lambda df: df[df["intent"] == "Unmet_Need"]),
    ("How do users discover products today?",
     lambda df: df[df["themes"].apply(lambda t: "discovery_UI" in (t or []))]),
]

with tab1:
    try:
        ex = load_extracted()
        for q, filt in STRATEGIC_QS:
            with st.expander(q, expanded=False):
                sub = filt(ex)
                st.write(f"**{len(sub)} snippets matched**")
                if len(sub) == 0:
                    st.info("No data yet — run the pipeline first.")
                    continue
                theme_counter = Counter()
                for lst in sub["themes"].dropna():
                    theme_counter.update(lst or [])
                if theme_counter:
                    tt = pd.DataFrame(theme_counter.most_common(8), columns=["theme", "count"])
                    st.plotly_chart(px.bar(tt, x="theme", y="count"), use_container_width=True)
                st.markdown("**Sample quotes:**")
                for _, row in sub[sub["actionable_quote"]].head(5).iterrows():
                    st.markdown(f"> {row['text'][:400]} \n> — *{row['source']}*")
    except Exception as e:
        st.error(f"Failed to load: {e}")

# ------------- Insight cards ------------------
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
                        sc = r["source_counts"] if isinstance(r["source_counts"], dict) else json.loads(r["source_counts"])
                        st.caption(" · ".join(f"{k}: {v}" for k, v in sc.items()))
                    st.markdown(f"**Suggested experiment:** {r['suggested_experiment']}")
    except Exception as e:
        st.error(f"Failed to load: {e}")

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
            # top barriers over time
            explode = ex.explode("themes")
            explode = explode[explode["themes"].notna()]
            byday = explode.groupby(["day", "themes"]).size().reset_index(name="n")
            top = explode["themes"].value_counts().head(5).index.tolist()
            byday = byday[byday["themes"].isin(top)]
            st.plotly_chart(
                px.line(byday, x="day", y="n", color="themes", title="Top 5 barriers over time"),
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
