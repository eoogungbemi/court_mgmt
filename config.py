from pathlib import Path
from dotenv import load_dotenv
import os

load_dotenv()

BASE_DIR = Path(__file__).parent
DATABASE_URL = f"sqlite:///{BASE_DIR}/court_mgmt.db"

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
LANGSMITH_API_KEY = os.getenv("LANGSMITH_API_KEY", "")
LANGSMITH_PROJECT = os.getenv("LANGSMITH_PROJECT", "court-mgmt")
LANGSMITH_TRACING = os.getenv("LANGSMITH_TRACING", "false").lower() == "true"

NUM_COURTROOMS = 20
NUM_LAWYERS = 40
COURT_START_HOUR = 9
COURT_END_HOUR = 17
BUFFER_BETWEEN_HEARINGS_MINS = 10
MAX_LAWYER_HEARINGS_PER_DAY = 8

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
