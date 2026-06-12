"""
Customer Intelligence Platform
================================
Enterprise-grade product review sentiment analysis dashboard.
Powered by Streamlit, TextBlob, and Plotly.
"""

from __future__ import annotations

import io
import json
import re
from collections import Counter
from datetime import datetime
from typing import Any

import nltk
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import PyPDF2
import streamlit as st
from textblob import TextBlob

# ---------------------------------------------------------------------------
# NLTK bootstrap (runs once per container)
# ---------------------------------------------------------------------------
for _pkg in ("punkt", "averaged_perceptron_tagger", "stopwords"):
    try:
        nltk.data.find(f"tokenizers/{_pkg}" if _pkg == "punkt" else f"corpora/{_pkg}" if _pkg == "stopwords" else f"taggers/{_pkg}")
    except LookupError:
        nltk.download(_pkg, quiet=True)

from nltk.corpus import stopwords
from nltk.tokenize import word_tokenize

_EN_STOPWORDS: set[str] = set(stopwords.words("english"))

# ---------------------------------------------------------------------------
# PAGE CONFIG
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Customer Intelligence Platform",
    page_icon="🧠",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# GLOBAL STYLES
# ---------------------------------------------------------------------------
st.markdown(
    """
    <style>
    /* ── Base ── */
    [data-testid="stAppViewContainer"] {
        background: #0d1117;
        color: #e6edf3;
    }
    [data-testid="stSidebar"] {
        background: #161b22;
        border-right: 1px solid #30363d;
    }
    /* ── Metric cards ── */
    [data-testid="metric-container"] {
        background: #161b22;
        border: 1px solid #30363d;
        border-radius: 10px;
        padding: 14px 18px;
    }
    [data-testid="metric-container"] label {
        color: #8b949e !important;
        font-size: 0.78rem;
        letter-spacing: 0.08em;
        text-transform: uppercase;
    }
    [data-testid="metric-container"] [data-testid="stMetricValue"] {
        font-size: 2rem;
        font-weight: 700;
        color: #e6edf3;
    }
    /* ── Tab strip ── */
    [data-baseweb="tab-list"] {
        gap: 6px;
        background: transparent;
        border-bottom: 1px solid #30363d;
    }
    [data-baseweb="tab"] {
        background: transparent !important;
        color: #8b949e !important;
        border-radius: 6px 6px 0 0;
        padding: 8px 18px;
        font-size: 0.85rem;
        font-weight: 500;
    }
    [aria-selected="true"][data-baseweb="tab"] {
        background: #21262d !important;
        color: #58a6ff !important;
        border-bottom: 2px solid #58a6ff !important;
    }
    /* ── Divider ── */
    hr {
        border-color: #30363d;
    }
    /* ── Download buttons ── */
    [data-testid="stDownloadButton"] > button {
        background: #21262d;
        border: 1px solid #30363d;
        color: #e6edf3;
        border-radius: 6px;
    }
    [data-testid="stDownloadButton"] > button:hover {
        background: #30363d;
        border-color: #58a6ff;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# SIDEBAR — NLP CONTROLS
# ---------------------------------------------------------------------------
st.sidebar.markdown("## 🧠 Customer Intelligence Platform")
st.sidebar.markdown("---")
st.sidebar.markdown("### Sentiment Thresholds")

positive_threshold: float = st.sidebar.slider(
    "Positive polarity floor",
    min_value=0.01,
    max_value=0.50,
    value=0.10,
    step=0.01,
    help="Polarity scores above this value are classified as Positive.",
)
negative_threshold: float = st.sidebar.slider(
    "Negative polarity ceiling",
    min_value=-0.50,
    max_value=-0.01,
    value=-0.10,
    step=0.01,
    help="Polarity scores below this value are classified as Negative.",
)
st.sidebar.markdown("---")
top_n_keywords: int = st.sidebar.slider(
    "Top N keywords to display",
    min_value=5,
    max_value=20,
    value=10,
    step=1,
)
st.sidebar.markdown("---")
st.sidebar.caption("Upload a PDF above to begin analysis.")

# ---------------------------------------------------------------------------
# BACKEND FUNCTIONS (cached)
# ---------------------------------------------------------------------------

@st.cache_data(show_spinner=False)
def extract_text_from_pdf(file_bytes: bytes) -> str:
    """
    Extract raw text from PDF bytes.
    Returns empty string on failure (non-raising).
    """
    text_parts: list[str] = []
    try:
        reader = PyPDF2.PdfReader(io.BytesIO(file_bytes))
        for page in reader.pages:
            extracted = page.extract_text()
            if extracted:
                text_parts.append(extracted)
    except PyPDF2.errors.PdfReadError as exc:
        st.error(f"PDF read error: {exc}")
    except Exception as exc:  # noqa: BLE001
        st.error(f"Unexpected error during PDF extraction: {exc}")
    return "\n".join(text_parts)


@st.cache_data(show_spinner=False)
def split_reviews(raw_text: str, min_length: int = 30) -> list[str]:
    """
    Intelligent fallback review splitter.

    Priority order:
      1. Explicit numbered patterns  e.g. "Review 3:", "3.", "3)"
      2. Date-string anchors         e.g. "January 12, 2024"
      3. Structural Pros/Cons blocks
      4. Double-newline paragraph breaks (original fallback)

    Fragments shorter than `min_length` characters are discarded.
    """
    # Pattern 1: numbered review markers
    numbered_pattern = re.compile(
        r"(?:^|\n)(?:Review\s*\d+\s*[:\-]|\d{1,3}[\.\)]\s+)",
        re.IGNORECASE | re.MULTILINE,
    )
    # Pattern 2: date anchors
    date_pattern = re.compile(
        r"(?:^|\n)(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
        r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
        r"\s+\d{1,2},?\s+\d{4}",
        re.IGNORECASE | re.MULTILINE,
    )
    # Pattern 3: structural markers
    structural_pattern = re.compile(
        r"(?:^|\n)(?:Pros\s*:|Cons\s*:|Rating\s*:|Title\s*:|Review\s*:)",
        re.IGNORECASE | re.MULTILINE,
    )

    def _apply_split(text: str, pattern: re.Pattern) -> list[str] | None:
        chunks = pattern.split(text)
        valid = [c.strip() for c in chunks if c and len(c.strip()) >= min_length]
        return valid if len(valid) > 1 else None

    for pattern in (numbered_pattern, date_pattern, structural_pattern):
        result = _apply_split(raw_text, pattern)
        if result:
            return result

    # Final fallback: double newline paragraph split
    fragments = re.split(r"\n\s*\n", raw_text)
    return [f.strip() for f in fragments if f.strip() and len(f.strip()) >= min_length]


@st.cache_data(show_spinner=False)
def extract_keywords(text: str, pos_tags: tuple[str, ...] = ("NN", "NNS", "JJ", "JJR")) -> list[str]:
    """
    Extract meaningful Nouns and Adjectives after removing English stopwords.
    Returns a list of lowercase keyword strings.
    """
    try:
        tokens = word_tokenize(text.lower())
        tagged = nltk.pos_tag(tokens)
        keywords = [
            word
            for word, tag in tagged
            if tag in pos_tags
            and word not in _EN_STOPWORDS
            and word.isalpha()
            and len(word) > 2
        ]
        return keywords
    except Exception:  # noqa: BLE001
        # Fallback: simple stopword removal without POS tagging
        tokens = re.findall(r"[a-z]+", text.lower())
        return [t for t in tokens if t not in _EN_STOPWORDS and len(t) > 3]


@st.cache_data(show_spinner=False)
def analyse_reviews(
    reviews: list[str],
    positive_threshold: float,
    negative_threshold: float,
) -> pd.DataFrame:
    """
    Run NLP feature engineering on every review and return enriched DataFrame.
    """
    records: list[dict[str, Any]] = []
    for idx, review_text in enumerate(reviews):
        blob = TextBlob(review_text)
        polarity: float = round(blob.sentiment.polarity, 4)
        subjectivity: float = round(blob.sentiment.subjectivity, 4)

        if polarity > positive_threshold:
            sentiment_label = "Positive"
        elif polarity < negative_threshold:
            sentiment_label = "Negative"
        else:
            sentiment_label = "Neutral"

        word_count: int = len(review_text.split())
        char_count: int = len(review_text)
        keywords: list[str] = extract_keywords(review_text)

        records.append(
            {
                "ID": f"REV-{idx + 1:04d}",
                "Full Review": review_text,
                "Sentiment": sentiment_label,
                "Polarity": polarity,
                "Subjectivity": subjectivity,
                "Word Count": word_count,
                "Char Count": char_count,
                "Keywords": ", ".join(keywords[:15]),  # store top 15 for display
                "_keywords_list": keywords,  # raw list for frequency analysis
            }
        )
    return pd.DataFrame(records)


# ---------------------------------------------------------------------------
# CHART HELPERS
# ---------------------------------------------------------------------------

SENTIMENT_COLORS: dict[str, str] = {
    "Positive": "#3fb950",
    "Negative": "#f85149",
    "Neutral": "#58a6ff",
}

_DARK_LAYOUT = dict(
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    font=dict(color="#e6edf3", family="Inter, system-ui, sans-serif"),
    margin=dict(t=50, b=30, l=20, r=20),
)


def _pie_chart(counts: Counter) -> go.Figure:
    labels = list(counts.keys())
    values = list(counts.values())
    colors = [SENTIMENT_COLORS.get(k, "#888") for k in labels]

    fig = go.Figure(
        data=[
            go.Pie(
                labels=labels,
                values=values,
                hole=0.45,
                marker=dict(
                    colors=colors,
                    line=dict(color="#0d1117", width=3),
                    pattern=dict(shape=["/", "x", "."], solidity=0.4),
                ),
                textfont=dict(size=13),
                hovertemplate="<b>%{label}</b><br>Count: %{value}<br>Share: %{percent}<extra></extra>",
            )
        ]
    )
    fig.update_layout(
        title=dict(text="Sentiment Distribution", font=dict(size=15)),
        showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=-0.15),
        **_DARK_LAYOUT,
    )
    return fig


def _bar_chart(counts: Counter) -> go.Figure:
    count_df = (
        pd.DataFrame(counts.items(), columns=["Sentiment", "Count"])
        .sort_values("Count", ascending=False)
    )
    fig = px.bar(
        count_df,
        x="Sentiment",
        y="Count",
        color="Sentiment",
        color_discrete_map=SENTIMENT_COLORS,
        text="Count",
    )
    fig.update_traces(textposition="outside", marker_line_width=0)
    fig.update_layout(
        title=dict(text="Review Frequency (ranked)", font=dict(size=15)),
        showlegend=False,
        xaxis=dict(gridcolor="#21262d"),
        yaxis=dict(gridcolor="#21262d"),
        **_DARK_LAYOUT,
    )
    return fig


def _polarity_histogram(df: pd.DataFrame) -> go.Figure:
    fig = px.histogram(
        df,
        x="Polarity",
        nbins=25,
        color_discrete_sequence=["#58a6ff"],
        marginal="box",
        opacity=0.85,
    )
    fig.update_layout(
        title=dict(text="Polarity Score Distribution", font=dict(size=15)),
        xaxis=dict(title="Polarity Score", gridcolor="#21262d"),
        yaxis=dict(title="Review Count", gridcolor="#21262d"),
        **_DARK_LAYOUT,
    )
    return fig


def _keyword_chart(df: pd.DataFrame, top_n: int) -> go.Figure:
    pos_reviews = df[df["Sentiment"] == "Positive"]["_keywords_list"].explode()
    neg_reviews = df[df["Sentiment"] == "Negative"]["_keywords_list"].explode()

    pos_counts = Counter(pos_reviews.dropna()).most_common(top_n)
    neg_counts = Counter(neg_reviews.dropna()).most_common(top_n)

    fig = go.Figure()

    if pos_counts:
        pw, pv = zip(*pos_counts)
        fig.add_trace(
            go.Bar(
                name="Positive",
                y=list(pw),
                x=list(pv),
                orientation="h",
                marker_color=SENTIMENT_COLORS["Positive"],
                hovertemplate="%{y}: %{x} mentions<extra></extra>",
            )
        )
    if neg_counts:
        nw, nv = zip(*neg_counts)
        fig.add_trace(
            go.Bar(
                name="Negative",
                y=list(nw),
                x=list(nv),
                orientation="h",
                marker_color=SENTIMENT_COLORS["Negative"],
                hovertemplate="%{y}: %{x} mentions<extra></extra>",
            )
        )

    fig.update_layout(
        title=dict(text=f"Top {top_n} Keywords — Positive vs Negative", font=dict(size=15)),
        barmode="group",
        xaxis=dict(title="Frequency", gridcolor="#21262d"),
        yaxis=dict(title="Keyword", autorange="reversed"),
        legend=dict(orientation="h", yanchor="bottom", y=-0.2),
        height=420,
        **_DARK_LAYOUT,
    )
    return fig


def _gauge_figure(avg_polarity: float) -> go.Figure:
    fig = go.Figure(
        go.Indicator(
            mode="gauge+number",
            value=avg_polarity,
            number=dict(suffix="", valueformat=".3f", font=dict(color="#e6edf3", size=28)),
            gauge=dict(
                axis=dict(range=[-1, 1], tickcolor="#8b949e"),
                bar=dict(color="#58a6ff"),
                bgcolor="#161b22",
                borderwidth=1,
                bordercolor="#30363d",
                steps=[
                    dict(range=[-1, -0.1], color="#2d1b1b"),
                    dict(range=[-0.1, 0.1], color="#1e2433"),
                    dict(range=[0.1, 1], color="#1b2d1e"),
                ],
                threshold=dict(
                    line=dict(color="#e6edf3", width=2),
                    thickness=0.75,
                    value=avg_polarity,
                ),
            ),
        )
    )
    fig.update_layout(
        height=220,
        margin=dict(t=30, b=10, l=20, r=20),
        paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#e6edf3"),
    )
    return fig


def _overall_flag(avg_polarity: float, pos_pct: float, neg_pct: float) -> tuple[str, str]:
    """Return (flag_text, css_color) contextual label for overall sentiment health."""
    if avg_polarity >= 0.3 and pos_pct >= 60:
        return "🌟 Highly Favorable", "#3fb950"
    elif avg_polarity >= 0.1 and pos_pct >= 40:
        return "✅ Generally Positive", "#58a6ff"
    elif avg_polarity <= -0.3 or neg_pct >= 50:
        return "🚨 Critical Care Required", "#f85149"
    elif avg_polarity < -0.1:
        return "⚠️ Needs Attention", "#d29922"
    else:
        return "➖ Mixed Sentiment", "#8b949e"


# ---------------------------------------------------------------------------
# EXPORT HELPERS
# ---------------------------------------------------------------------------

def _build_csv(df: pd.DataFrame) -> bytes:
    export_cols = ["ID", "Sentiment", "Polarity", "Subjectivity", "Word Count", "Char Count", "Keywords", "Full Review"]
    return df[export_cols].to_csv(index=False).encode("utf-8")


def _build_json(df: pd.DataFrame, metadata: dict[str, Any]) -> bytes:
    payload = {
        "meta": metadata,
        "summary": {
            "total_reviews": len(df),
            "sentiment_counts": df["Sentiment"].value_counts().to_dict(),
            "avg_polarity": round(df["Polarity"].mean(), 4),
            "avg_subjectivity": round(df["Subjectivity"].mean(), 4),
        },
        "reviews": df.drop(columns=["_keywords_list"]).to_dict(orient="records"),
    }
    return json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")


# ---------------------------------------------------------------------------
# MAIN APP
# ---------------------------------------------------------------------------

st.markdown(
    "<h1 style='margin-bottom:0;color:#e6edf3;font-size:1.7rem;font-weight:700;'>🧠 Customer Intelligence Platform</h1>"
    "<p style='color:#8b949e;margin-top:4px;font-size:0.9rem;'>Upload a product review PDF to generate deep sentiment intelligence.</p>",
    unsafe_allow_html=True,
)

uploaded_file = st.file_uploader("Upload product reviews PDF", type="pdf", label_visibility="collapsed")

if not uploaded_file:
    st.info("👆 Upload a PDF containing product reviews to begin.", icon="📄")
    st.stop()

# ---- Process ----
file_bytes: bytes = uploaded_file.read()

with st.spinner("Extracting text from PDF…"):
    raw_text: str = extract_text_from_pdf(file_bytes)

if not raw_text.strip():
    st.error("Could not extract readable text from this PDF. Try a text-based (non-scanned) PDF.")
    st.stop()

with st.spinner("Splitting and parsing reviews…"):
    reviews: list[str] = split_reviews(raw_text)

if not reviews:
    st.warning("No valid review fragments found. The PDF may not contain multi-review content.")
    st.stop()

with st.spinner(f"Running NLP analysis on {len(reviews)} reviews…"):
    df: pd.DataFrame = analyse_reviews(reviews, positive_threshold, negative_threshold)

# Derived stats
sentiment_counts: Counter = Counter(df["Sentiment"].tolist())
total: int = len(df)
avg_polarity: float = round(df["Polarity"].mean(), 4)
avg_subjectivity: float = round(df["Subjectivity"].mean(), 4)
pos_pct: float = (sentiment_counts.get("Positive", 0) / total) * 100
neg_pct: float = (sentiment_counts.get("Negative", 0) / total) * 100
flag_text, flag_color = _overall_flag(avg_polarity, pos_pct, neg_pct)

metadata: dict[str, Any] = {
    "filename": uploaded_file.name,
    "generated_at": datetime.utcnow().isoformat() + "Z",
    "positive_threshold": positive_threshold,
    "negative_threshold": negative_threshold,
    "total_reviews_parsed": total,
}

# ---------------------------------------------------------------------------
# TABS
# ---------------------------------------------------------------------------
tab_exec, tab_nlp, tab_explorer = st.tabs(
    ["📊 Executive Summary", "🔍 Deep-Dive NLP Insights", "🗃️ Interactive Data Explorer"]
)

# ── TAB 1: Executive Summary ─────────────────────────────────────────────
with tab_exec:
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("✅ Positive", sentiment_counts.get("Positive", 0), f"{pos_pct:.1f}%")
    m2.metric("❌ Negative", sentiment_counts.get("Negative", 0), f"{neg_pct:.1f}%")
    m3.metric("➖ Neutral", sentiment_counts.get("Neutral", 0), f"{(sentiment_counts.get('Neutral',0)/total)*100:.1f}%")
    m4.metric("📏 Avg Polarity", f"{avg_polarity:+.3f}", flag_text)

    st.markdown(
        f"<div style='text-align:center;padding:10px 0 4px;font-size:1.1rem;font-weight:600;color:{flag_color};'>"
        f"{flag_text}</div>",
        unsafe_allow_html=True,
    )

    st.divider()

    col_pie, col_gauge, col_bar = st.columns([2, 1.4, 2])
    with col_pie:
        st.plotly_chart(_pie_chart(sentiment_counts), use_container_width=True)
    with col_gauge:
        st.markdown("<p style='text-align:center;color:#8b949e;font-size:0.82rem;margin-bottom:0;'>AVERAGE POLARITY GAUGE</p>", unsafe_allow_html=True)
        st.plotly_chart(_gauge_figure(avg_polarity), use_container_width=True)
    with col_bar:
        st.plotly_chart(_bar_chart(sentiment_counts), use_container_width=True)

# ── TAB 2: Deep-Dive NLP Insights ────────────────────────────────────────
with tab_nlp:
    col_hist, col_kw = st.columns(2)

    with col_hist:
        st.plotly_chart(_polarity_histogram(df), use_container_width=True)

        subj_fig = px.histogram(
            df,
            x="Subjectivity",
            nbins=20,
            color_discrete_sequence=["#d29922"],
            opacity=0.85,
            marginal="rug",
        )
        subj_fig.update_layout(
            title=dict(text="Subjectivity Distribution", font=dict(size=15)),
            xaxis=dict(title="Subjectivity Score (0=Objective, 1=Subjective)", gridcolor="#21262d"),
            yaxis=dict(gridcolor="#21262d"),
            **_DARK_LAYOUT,
        )
        st.plotly_chart(subj_fig, use_container_width=True)

    with col_kw:
        st.plotly_chart(_keyword_chart(df, top_n_keywords), use_container_width=True)

        # Scatter: polarity vs subjectivity
        scatter_fig = px.scatter(
            df,
            x="Polarity",
            y="Subjectivity",
            color="Sentiment",
            color_discrete_map=SENTIMENT_COLORS,
            hover_data={"ID": True, "Word Count": True},
            opacity=0.75,
            size_max=10,
        )
        scatter_fig.update_layout(
            title=dict(text="Polarity vs Subjectivity Map", font=dict(size=15)),
            xaxis=dict(gridcolor="#21262d"),
            yaxis=dict(gridcolor="#21262d"),
            **_DARK_LAYOUT,
        )
        st.plotly_chart(scatter_fig, use_container_width=True)

# ── TAB 3: Interactive Data Explorer ─────────────────────────────────────
with tab_explorer:
    st.markdown("#### Filter Reviews")

    fc1, fc2, fc3 = st.columns([2, 1.5, 2])

    with fc1:
        keyword_query: str = st.text_input("🔎 Search keywords in review text", placeholder="e.g. battery, screen, value")

    with fc2:
        selected_sentiments: list[str] = st.multiselect(
            "Sentiment category",
            options=["Positive", "Negative", "Neutral"],
            default=["Positive", "Negative", "Neutral"],
        )

    with fc3:
        min_wc: int = int(df["Word Count"].min())
        max_wc: int = int(df["Word Count"].max())
        if min_wc < max_wc:
            wc_range: tuple[int, int] = st.slider(
                "Word count range",
                min_value=min_wc,
                max_value=max_wc,
                value=(min_wc, max_wc),
            )
        else:
            wc_range = (min_wc, max_wc)
            st.info(f"All reviews are ~{min_wc} words.")

    # Apply filters
    mask = (
        df["Sentiment"].isin(selected_sentiments)
        & df["Word Count"].between(wc_range[0], wc_range[1])
    )
    if keyword_query.strip():
        mask &= df["Full Review"].str.contains(keyword_query.strip(), case=False, na=False)

    filtered_df: pd.DataFrame = df[mask]

    st.markdown(
        f"<p style='color:#8b949e;font-size:0.82rem;'>Showing <b style='color:#e6edf3;'>{len(filtered_df)}</b> of <b style='color:#e6edf3;'>{total}</b> reviews</p>",
        unsafe_allow_html=True,
    )

    display_cols = ["ID", "Sentiment", "Polarity", "Subjectivity", "Word Count", "Char Count", "Keywords"]
    st.dataframe(
        filtered_df[display_cols].style.background_gradient(
            subset=["Polarity"], cmap="RdYlGn", vmin=-1, vmax=1
        ),
        use_container_width=True,
        height=420,
    )

    # Review reader
    if not filtered_df.empty:
        st.markdown("#### Read full review")
        rev_id: str = st.selectbox("Select review ID", filtered_df["ID"].tolist())
        selected_row = filtered_df[filtered_df["ID"] == rev_id].iloc[0]
        sentiment_badge_color = SENTIMENT_COLORS.get(selected_row["Sentiment"], "#888")
        st.markdown(
            f"<div style='background:#161b22;border:1px solid #30363d;border-left:4px solid {sentiment_badge_color};"
            f"border-radius:8px;padding:16px 20px;'>"
            f"<span style='font-size:0.75rem;color:#8b949e;'>{selected_row['ID']} · "
            f"<span style='color:{sentiment_badge_color};'>{selected_row['Sentiment']}</span> · "
            f"Polarity: {selected_row['Polarity']:+.3f} · Subjectivity: {selected_row['Subjectivity']:.3f}</span>"
            f"<p style='margin-top:10px;font-size:0.95rem;color:#e6edf3;line-height:1.7;'>{selected_row['Full Review']}</p>"
            f"</div>",
            unsafe_allow_html=True,
        )

    st.divider()
    st.markdown("#### Export")
    dl1, dl2 = st.columns(2)
    with dl1:
        st.download_button(
            label="📥 Download CSV Report",
            data=_build_csv(df),
            file_name="customer_intelligence_report.csv",
            mime="text/csv",
            use_container_width=True,
        )
    with dl2:
        st.download_button(
            label="📦 Download Full JSON Payload",
            data=_build_json(df, metadata),
            file_name="customer_intelligence_payload.json",
            mime="application/json",
            use_container_width=True,
        )
