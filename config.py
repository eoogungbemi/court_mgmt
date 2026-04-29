from pathlib import Path
from dotenv import load_dotenv
import os

load_dotenv()


def _secret(key: str, default: str = "") -> str:
    """
    Read from Streamlit secrets first (cloud deployment), then fall back
    to environment variables (local / CI). Safe to call outside Streamlit.
    """
    try:
        import streamlit as st
        val = st.secrets.get(key)
        if val:
            return str(val)
    except Exception:
        pass
    return os.getenv(key, default)


BASE_DIR = Path(__file__).parent
DATABASE_URL = f"sqlite:///{BASE_DIR}/court_mgmt.db"

ANTHROPIC_API_KEY  = _secret("ANTHROPIC_API_KEY")
LANGSMITH_API_KEY  = _secret("LANGSMITH_API_KEY")
LANGSMITH_PROJECT  = _secret("LANGSMITH_PROJECT", "court-mgmt")
LANGSMITH_TRACING  = _secret("LANGSMITH_TRACING", "false").lower() == "true"

NUM_COURTROOMS = 20
NUM_LAWYERS    = 40
COURT_START_HOUR = 9
COURT_END_HOUR   = 17
BUFFER_BETWEEN_HEARINGS_MINS = 10
MAX_LAWYER_HEARINGS_PER_DAY  = 8

HEARING_DURATIONS = {
    "criminal": {
        "arraignment":          (10, 20),
        "bail_hearing":         (15, 30),
        "preliminary_hearing":  (30, 60),
        "motion_hearing":       (20, 45),
        "plea_hearing":         (15, 30),
        "sentencing":           (20, 45),
        "bench_trial":          (60, 180),
    },
    "civil": {
        "status_conference":    (15, 30),
        "motion_hearing":       (30, 60),
        "bench_trial":          (90, 240),
    },
    "traffic": {
        "arraignment":          (5, 15),
        "plea_hearing":         (10, 20),
        "bench_trial":          (30, 90),
    },
}

COMPLEXITY_MULTIPLIER = {"low": 0.8, "medium": 1.0, "high": 1.5}

# Default is intentionally weak — override via CLERK_PASSWORD env var or st.secrets
CLERK_PASSWORD = _secret("CLERK_PASSWORD", "clerk123")
