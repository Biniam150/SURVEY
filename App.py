import streamlit as st
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime
import json
import re

st.set_page_config(
    page_title="Water Demand Query Collector",
    page_icon="💧",
    layout="centered"
)

# ── Styling ──────────────────────────────────────────────────────────────────
st.markdown("""
<style>
    .main { max-width: 700px; }
    .stTextArea textarea { font-size: 15px; }
    .tag {
        display: inline-block;
        padding: 3px 10px;
        border-radius: 999px;
        font-size: 12px;
        font-weight: 600;
        margin: 2px;
    }
    .tag-green  { background: #E1F5EE; color: #0F6E56; }
    .tag-blue   { background: #E6F1FB; color: #185FA5; }
    .tag-gray   { background: #F1EFE8; color: #5F5E5A; }
    .result-box {
        background: #f8fffe;
        border: 1px solid #9FE1CB;
        border-radius: 10px;
        padding: 1rem 1.25rem;
        margin-top: 1rem;
    }
    .quote { border-left: 3px solid #1D9E75; padding-left: 10px; color: #444; font-style: italic; margin-bottom: 10px; }
    h1 { font-size: 26px !important; }
</style>
""", unsafe_allow_html=True)

# ── Rule-based classifier (no API needed) ────────────────────────────────────
MODEL_RULES = {
    "demand_forecasting": {
        "keywords": ["forecast", "predict", "tomorrow", "next week", "next hour", "will demand", "expected demand",
                     "consumption tomorrow", "usage next", "how much water", "peak demand", "daily demand",
                     "projected", "estimate demand", "future demand"],
        "label": "Demand forecasting",
        "color": "tag-green"
    },
    "anomaly_detection": {
        "keywords": ["anomaly", "unusual", "abnormal", "leak", "burst", "fault", "meter fault", "strange",
                     "unexpected", "spike", "sudden", "drop in pressure", "negative flow", "zero consumption",
                     "is something wrong", "alert", "alarm"],
        "label": "Anomaly detection",
        "color": "tag-blue"
    },
    "scenario_generation": {
        "keywords": ["what if", "scenario", "drought", "event", "population growth", "new development",
                     "stress test", "extreme", "planning", "simulate", "hypothetical", "if demand increases",
                     "what happens if", "under conditions"],
        "label": "Scenario generation",
        "color": "tag-green"
    },
    "digital_twin": {
        "keywords": ["pressure", "hydraulic", "pipe", "network", "simulation", "zone", "reservoir level",
                     "pump", "valve", "tank", "model the system", "system state", "flow in pipe"],
        "label": "Digital twin / hydraulic",
        "color": "tag-blue"
    },
    "pattern_analysis": {
        "keywords": ["pattern", "trend", "seasonal", "weekly", "monthly", "historical", "last year",
                     "compare", "typical", "average consumption", "behavior", "usage profile", "diurnal"],
        "label": "Pattern analysis",
        "color": "tag-green"
    },
    "reporting": {
        "keywords": ["report", "summary", "total consumption", "billing", "compliance", "regulatory",
                     "water loss", "non-revenue water", "nrw", "statistics", "kpi", "dashboard"],
        "label": "Reporting / analytics",
        "color": "tag-gray"
    },
}

def classify_question(question: str) -> dict:
    q = question.lower()
    scores = {}
    for model, info in MODEL_RULES.items():
        score = sum(1 for kw in info["keywords"] if kw in q)
        if score > 0:
            scores[model] = score

    if not scores:
        return {
            "primary_model": "out_of_scope",
            "label": "Out of scope",
            "color": "tag-gray",
            "secondary": [],
            "reasoning": "No clear match to a water demand model category."
        }

    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    primary = ranked[0][0]
    secondary = [MODEL_RULES[m[0]]["label"] for m in ranked[1:3]]

    # Time horizon
    time_horizon = "not specified"
    if any(w in q for w in ["real-time", "right now", "current", "live", "now"]):
        time_horizon = "real-time"
    elif any(w in q for w in ["tomorrow", "next hour", "tonight", "today"]):
        time_horizon = "short-term"
    elif any(w in q for w in ["next week", "this week", "coming days"]):
        time_horizon = "medium-term"
    elif any(w in q for w in ["next month", "next year", "long-term", "future"]):
        time_horizon = "long-term"
    elif any(w in q for w in ["last year", "historical", "past", "previous"]):
        time_horizon = "historical"

    return {
        "primary_model": primary,
        "label": MODEL_RULES[primary]["label"],
        "color": MODEL_RULES[primary]["color"],
        "secondary": secondary,
        "time_horizon": time_horizon,
        "reasoning": f"Question matches '{MODEL_RULES[primary]['label']}' based on key terms detected."
    }

# ── Google Sheets connection ──────────────────────────────────────────────────
def get_sheet():
    try:
        creds_dict = st.secrets["gcp_service_account"]
        scopes = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        client = gspread.authorize(creds)
        sheet = client.open(st.secrets["sheet_name"]).sheet1
        # Ensure headers exist
        if sheet.row_count == 0 or sheet.cell(1, 1).value != "Timestamp":
            sheet.insert_row(["Timestamp", "Role", "Organization", "Question", "Primary Model", "Secondary Models", "Time Horizon", "Reasoning"], 1)
        return sheet
    except Exception as e:
        return None

def save_to_sheet(sheet, entry: dict):
    if sheet is None:
        return False
    try:
        sheet.append_row([
            entry["timestamp"],
            entry["role"],
            entry["organization"],
            entry["question"],
            entry["label"],
            ", ".join(entry.get("secondary", [])),
            entry.get("time_horizon", ""),
            entry.get("reasoning", "")
        ])
        return True
    except:
        return False

# ── UI ────────────────────────────────────────────────────────────────────────
st.markdown("## 💧 Water demand operator query collector")
st.markdown(
    "This tool is part of a research study at **UBC Okanagan** on AI-assisted water demand management. "
    "Enter the questions you ask daily about your water system — your input helps identify which analytical models are most needed."
)
st.markdown("---")

col1, col2 = st.columns(2)
with col1:
    role = st.selectbox("Your role", ["Select...", "Operator", "Engineer", "Manager / Supervisor", "Planner", "Other"])
with col2:
    org = st.text_input("Organization (optional)", placeholder="e.g. City of Kelowna")

question = st.text_area(
    "What question do you ask about your water system day-to-day?",
    placeholder="e.g. What will demand be at zone 3 tomorrow morning?",
    height=100
)

submitted = st.button("Submit question", type="primary", use_container_width=True)

if submitted:
    if not question.strip():
        st.warning("Please enter a question before submitting.")
    elif role == "Select...":
        st.warning("Please select your role.")
    else:
        result = classify_question(question)

        entry = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "role": role,
            "organization": org or "not provided",
            "question": question.strip(),
            **result
        }

        # Save to Google Sheets
        sheet = get_sheet()
        saved = save_to_sheet(sheet, entry)

        # Show result
        tags_html = f'<span class="tag {result["color"]}">{result["label"]}</span>'
        for sec in result.get("secondary", []):
            tags_html += f' <span class="tag tag-blue">{sec}</span>'
        if result.get("time_horizon") and result["time_horizon"] != "not specified":
            tags_html += f' <span class="tag tag-gray">{result["time_horizon"]}</span>'

        st.markdown(f"""
        <div class="result-box">
            <div class="quote">"{question.strip()}"</div>
            <div style="margin-bottom: 8px;">{tags_html}</div>
            <div style="font-size: 13px; color: #555;">{result["reasoning"]}</div>
        </div>
        """, unsafe_allow_html=True)

        if saved:
            st.success("Response recorded. Thank you!")
        elif sheet is None:
            st.info("Response classified. (Google Sheets not configured — running in local mode.)")
        else:
            st.warning("Classified but could not save to sheet. Check your credentials.")

        # Encourage another question
        st.markdown("**Have more questions?** Feel free to submit again — each question helps.")

st.markdown("---")
st.markdown(
    "<div style='font-size: 12px; color: #888; text-align: center;'>"
    "UBC Okanagan · Civil Engineering · Water Systems & AI Research · "
    "Questions? Contact the research team."
    "</div>",
    unsafe_allow_html=True
)