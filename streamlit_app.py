import streamlit as st
import pandas as pd
import PyPDF2
from textblob import TextBlob
import plotly.graph_objects as go
import plotly.express as px  # Added Plotly Express
from collections import Counter # Added Counter
import re
import io

# --- STAGE 2: PAGE CONFIGURATION ---
st.set_page_config(
    page_title="Product Review Sentiment Analysis",
    page_icon="📊",
    layout="wide"
)

st.title("📊 Product Review Sentiment Analysis")

# --- STAGE 5: SENTIMENT SCORING LOGIC ---
def get_sentiment(text):
    blob = TextBlob(text)
    polarity = blob.sentiment.polarity
    if polarity > 0.1:
        return "Positive", round(polarity, 2)
    elif polarity < -0.1:
        return "Negative", round(polarity, 2)
    else:
        return "Neutral", round(polarity, 2)

# --- STAGE 3: PDF EXTRACTION ---
def extract_text_from_pdf(file):
    try:
        reader = PyPDF2.PdfReader(file)
        text = ""
        for page in reader.pages:
            extracted = page.extract_text()
            if extracted: text += extracted + "\n"
        return text
    except Exception as e:
        st.error(f"Error reading PDF: {e}")
        return ""

# --- STAGE 4: REVIEW SPLITTING ---
def split_reviews(text):
    fragments = re.split(r'\n\s*\n', text)
    return [r.strip() for r in fragments if r.strip() and len(r.strip()) > 20]

uploaded_file = st.file_uploader("Upload your product reviews PDF", type="pdf")

if uploaded_file:
    with st.spinner("Analyzing..."):
        raw_text = extract_text_from_pdf(uploaded_file)
        reviews = split_reviews(raw_text)
    
    if reviews:
        # --- STAGE 6: DATA PREPARATION ---
        data = []
        sentiment_list = [] # For Counter
        for i, r in enumerate(reviews):
            label, score = get_sentiment(r)
            sentiment_list.append(label)
            data.append({
                "ID": f"REV-{i+1:03}",
                "Full Review": r,
                "Sentiment": label,
                "Score": score
            })
        df = pd.DataFrame(data)

        # --- USE COLLECTIONS.COUNTER ---
        counts_dict = Counter(sentiment_list)
        
        # --- STAGE 7: SUMMARY METRICS ---
        m1, m2, m3 = st.columns(3)
        for col, label in zip([m1, m2, m3], ["Positive", "Negative", "Neutral"]):
            count = counts_dict[label]
            percent = (count / len(df)) * 100
            col.metric(label, count, f"{percent:.1f}%")

        # --- STAGE 8: VISUALIZATIONS ---
        st.divider()
        col1, col2 = st.columns(2)

        # Accessible Graph Objects Pie Chart (with Patterns)
        with col1:
            color_map = {"Positive": "#2ecc71", "Negative": "#e74c3c", "Neutral": "#3498db"}
            fig_pie = go.Figure(data=[go.Pie(
                labels=list(counts_dict.keys()),
                values=list(counts_dict.values()),
                marker=dict(
                    colors=[color_map[k] for k in counts_dict.keys()],
                    line=dict(color='#000000', width=2),
                    pattern_shape=["/", "x", "."]
                )
            )])
            fig_pie.update_layout(title="Sentiment Distribution (Graph Objects)")
            st.plotly_chart(fig_pie, use_container_width=True)

        # Modern Plotly Express Bar Chart
        with col2:
            # Converting Counter to DataFrame for PX
            count_df = pd.DataFrame(counts_dict.items(), columns=['Sentiment', 'Count'])
            fig_bar = px.bar(
                count_df, 
                x='Sentiment', 
                y='Count', 
                color='Sentiment',
                color_discrete_map=color_map,
                title="Review Frequency (Plotly Express)"
            )
            st.plotly_chart(fig_bar, use_container_width=True)

        # Plotly Express Histogram for Polarity
        st.subheader("Polarity Analysis")
        fig_hist = px.histogram(
            df, 
            x="Score", 
            nbins=20, 
            color_discrete_sequence=['#9b59b6'],
            marginal="rug", # Adds distribution points at the bottom
            title="Sentiment Polarity Spread"
        )
        st.plotly_chart(fig_hist, use_container_width=True)

        # --- DATA EXPLORER & EXPORT ---
        st.divider()
        st.dataframe(df[["ID", "Sentiment", "Score"]], use_container_width=True)
        
        csv = df.to_csv(index=False).encode('utf-8')
        st.download_button("📥 Download Report", csv, "analysis.csv", "text/csv")
    else:
        st.warning("No reviews found in PDF.")
