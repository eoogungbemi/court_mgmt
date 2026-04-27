import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pathlib import Path
from datetime import datetime
import streamlit as st
import pandas as pd

from db.database import init_db, SessionLocal
from db.models import Courtroom
from sqlalchemy import select

st.set_page_config(
    page_title="Evaluation · Court",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed",
)
init_db()

RESULTS_DIR = Path(__file__).parent.parent / "evaluation" / "results"
RESULTS_DIR.mkdir(exist_ok=True)

st.sidebar.page_link("Home.py", label="← Back to Home", icon="⚖️")

st.markdown("## 📊 System Evaluation")
st.caption(
    "Compares three scheduling conditions across all 20 courtrooms × 5 days. "
    "Measures waiting time reduction, ETA range quality, and conflict detection accuracy."
)
st.page_link("Home.py", label="← Back to Home", icon="⚖️")
st.divider()


# ── Helpers ───────────────────────────────────────────────────────────────────

def get_latest_run() -> dict | None:
    """Return paths for the most recent evaluation run, or None."""
    summaries = sorted(RESULTS_DIR.glob("summary_*.csv"), reverse=True)
    if not summaries:
        return None
    ts = summaries[0].stem.replace("summary_", "")
    charts = sorted(RESULTS_DIR.glob(f"{ts}_*.png"))
    conflict_csv = RESULTS_DIR / f"conflict_{ts}.csv"
    raw_csv = RESULTS_DIR / f"raw_{ts}.csv"
    return {
        "ts": ts,
        "summary": summaries[0],
        "charts": charts,
        "conflict": conflict_csv if conflict_csv.exists() else None,
        "raw": raw_csv if raw_csv.exists() else None,
        "dt": datetime.strptime(ts, "%Y%m%d_%H%M%S"),
    }


def run_evaluation(use_llm: bool) -> dict:
    """Execute the full evaluation and return the new run's paths."""
    from evaluation.simulator import simulate_courtroom_day, HearingResult
    from evaluation.metrics import compute, ScenarioMetrics
    from evaluation import conflict_test
    from sqlalchemy import func

    db = SessionLocal()

    # Collect all dates in DB
    from db.models import Hearing
    dates = db.execute(
        select(func.date(Hearing.scheduled_start).label("d"))
        .distinct().order_by("d")
    ).scalars().all()
    dates = [str(d) for d in dates]

    courtrooms = db.execute(select(Courtroom).order_by(Courtroom.id)).scalars().all()

    all_results: list[HearingResult] = []
    progress = st.progress(0, text="Running simulations…")
    total_steps = len(courtrooms) * len(dates)
    step = 0

    for day in dates:
        for cr in courtrooms:
            sim = simulate_courtroom_day(
                courtroom_id=cr.id,
                run_date=day,
                db=db,
                seed=42,
                use_llm=use_llm,
            )
            all_results.extend(sim)
            step += 1
            progress.progress(step / total_steps,
                              text=f"Simulating {cr.name} · {day}")

    progress.empty()

    base_m  = compute(all_results, "baseline")
    rules_m = compute(all_results, "rules")
    full_m  = compute(all_results, "full")

    # Conflict detection test
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    detection = None
    try:
        injected  = conflict_test.inject_conflicts(db, n_conflicts=10, run_id=ts)
        detection = conflict_test.run_detection(injected, db)
    except Exception:
        pass
    finally:
        conflict_test.cleanup(db)

    # Save CSVs
    import csv
    summary_path = RESULTS_DIR / f"summary_{ts}.csv"
    with open(summary_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=base_m.__dataclass_fields__.keys())
        w.writeheader()
        w.writerows([m.__dict__ for m in [base_m, rules_m, full_m]])

    raw_path = RESULTS_DIR / f"raw_{ts}.csv"
    with open(raw_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["hearing_id", "date", "case_type", "hearing_type", "complexity",
                    "position", "scheduled_duration_mins", "actual_duration_mins",
                    "baseline_wait_mins", "rules_wait_mins", "full_wait_mins",
                    "rules_p25", "rules_p75", "rules_in_range",
                    "full_p25", "full_p75", "full_in_range", "full_used_llm"])
        for r in all_results:
            w.writerow([
                r.hearing_id, r.scheduled_start.strftime("%Y-%m-%d"),
                r.case_type, r.hearing_type, r.complexity, r.position,
                r.scheduled_duration_mins, r.actual_duration_mins,
                round(r.baseline_wait_mins, 2),
                round(r.rules_wait_mins, 2),
                round(r.full_wait_mins, 2),
                r.rules_p25, r.rules_p75, r.rules_in_range,
                r.full_p25, r.full_p75, r.full_in_range, r.full_used_llm,
            ])

    if detection:
        conflict_path = RESULTS_DIR / f"conflict_{ts}.csv"
        with open(conflict_path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["n_injected", "n_detected", "false_positives",
                        "precision", "recall", "f1"])
            w.writerow([detection.n_injected, detection.n_detected,
                        detection.n_false_positives, detection.precision,
                        detection.recall, detection.f1])

    # Generate charts
    from evaluation.visualize import generate_all
    generate_all(all_results, base_m, rules_m, full_m, detection, RESULTS_DIR, ts)

    db.close()
    return get_latest_run()


# ── Run controls ──────────────────────────────────────────────────────────────

latest = get_latest_run()

ctrl_col, info_col = st.columns([2, 3])
with ctrl_col:
    with st.container(border=True):
        st.markdown("**Run Evaluation**")
        use_llm = st.toggle(
            "Enable LLM duration estimates",
            value=False,
            help="Uses Claude Haiku to estimate P25–P75 ranges. "
                 "Requires ANTHROPIC_API_KEY in .env. "
                 "Falls back to rules automatically if unavailable.",
        )
        if st.button("▶ Run Now", type="primary", use_container_width=True):
            with st.spinner("Running 20 courtrooms × 5 days…"):
                latest = run_evaluation(use_llm)
            st.success("Evaluation complete.")
            st.rerun()

with info_col:
    if latest:
        st.info(
            f"Showing results from **{latest['dt'].strftime('%d %b %Y at %I:%M %p')}**. "
            f"Click **Run Now** to refresh with current data.",
            icon="📅",
        )
    else:
        st.warning("No evaluation results yet. Click **Run Now** to generate them.", icon="⚠️")

if not latest:
    st.stop()

st.divider()

# ── Load data ─────────────────────────────────────────────────────────────────

summary_df = pd.read_csv(latest["summary"])
# Ensure consistent scenario order
order = {"baseline": 0, "rules": 1, "full": 2}
summary_df["_order"] = summary_df["scenario"].map(order)
summary_df = summary_df.sort_values("_order").drop(columns="_order").reset_index(drop=True)

base_row  = summary_df[summary_df.scenario == "baseline"].iloc[0]
rules_row = summary_df[summary_df.scenario == "rules"].iloc[0]
full_row  = summary_df[summary_df.scenario == "full"].iloc[0]


# ── Headline metrics ──────────────────────────────────────────────────────────

st.markdown("### Key Results")

m1, m2, m3, m4 = st.columns(4)

wait_delta_rules = rules_row.avg_wait_mins - base_row.avg_wait_mins
wait_delta_full  = full_row.avg_wait_mins  - base_row.avg_wait_mins

m1.metric(
    "Avg wait — Baseline",
    f"{base_row.avg_wait_mins:.1f} min",
    help="Static schedule, no agent adaptation",
)
m2.metric(
    "Avg wait — Rules",
    f"{rules_row.avg_wait_mins:.1f} min",
    delta=f"{wait_delta_rules:.1f} min",
    delta_color="inverse",
    help="P75-buffered docket, rule-based estimates",
)
m3.metric(
    "Avg wait — Full system",
    f"{full_row.avg_wait_mins:.1f} min",
    delta=f"{wait_delta_full:.1f} min",
    delta_color="inverse",
    help="P75-buffered docket, LLM estimates where available",
)
m4.metric(
    "ETA Range Quality",
    f"{rules_row.eta_range_quality:.1f}%",
    help="% of hearings where actual duration fell within the P25–P75 predicted range",
)

st.divider()

# ── Full comparison table ─────────────────────────────────────────────────────

st.markdown("### Full Metrics Comparison")

METRIC_LABELS = {
    "n_hearings":          ("Hearings simulated",       ""),
    "avg_wait_mins":       ("Avg waiting time",         "min  ↓ better"),
    "median_wait_mins":    ("Median waiting time",      "min  ↓ better"),
    "p90_wait_mins":       ("P90 waiting time",         "min  ↓ better"),
    "pct_delayed":         ("% hearings delayed",       "%    ↓ better"),
    "avg_idle_gap_mins":   ("Avg idle gap",             "min"),
    "total_idle_mins":     ("Total idle time",          "min"),
    "eta_range_quality":   ("ETA range quality",        "%    ↑ better"),
    "overrun_rate":        ("Overrun rate",             "%"),
}

table_rows = []
for col, (label, note) in METRIC_LABELS.items():
    b = base_row.get(col,  "—")
    r = rules_row.get(col, "—")
    f = full_row.get(col,  "—")
    if col == "eta_range_quality" and b == 0.0:
        b = "—"
    table_rows.append({
        "Metric": label,
        "Baseline": b,
        "Rules-only": r,
        "Full system": f,
        "Note": note,
    })

table_df = pd.DataFrame(table_rows)
st.dataframe(
    table_df,
    use_container_width=True,
    hide_index=True,
    column_config={
        "Metric":      st.column_config.TextColumn(width="medium"),
        "Baseline":    st.column_config.NumberColumn(format="%.2f"),
        "Rules-only":  st.column_config.NumberColumn(format="%.2f"),
        "Full system": st.column_config.NumberColumn(format="%.2f"),
        "Note":        st.column_config.TextColumn(width="small"),
    },
)

# Wait reduction callout
pct_reduction = (
    (base_row.avg_wait_mins - rules_row.avg_wait_mins)
    / max(base_row.avg_wait_mins, 0.01) * 100
)
st.success(
    f"**Rules-only agents reduce average waiting time by "
    f"{pct_reduction:.1f}%** compared to the static baseline "
    f"({base_row.avg_wait_mins:.1f} min → {rules_row.avg_wait_mins:.1f} min). "
    f"The tradeoff: average idle gap increases from "
    f"{base_row.avg_idle_gap_mins:.1f} min to {rules_row.avg_idle_gap_mins:.1f} min."
)

st.divider()

# ── Conflict detection ────────────────────────────────────────────────────────

if latest["conflict"]:
    st.markdown("### Conflict Detection Accuracy")
    conflict_df = pd.read_csv(latest["conflict"])
    row = conflict_df.iloc[0]

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Injected conflicts",  int(row.n_injected))
    c2.metric("Detected",            int(row.n_detected))
    c3.metric("Precision",           f"{row.precision:.3f}")
    c4.metric("Recall",              f"{row.recall:.3f}")
    c5.metric("F1 Score",            f"{row.f1:.3f}")

    if row.precision == 1.0 and row.recall == 1.0:
        st.success(
            "**Perfect detection.** All injected conflicts caught with zero false positives. "
            "The ConflictDetectorAgent correctly identifies every lawyer appearing in two "
            "courtrooms at the same time."
        )
    st.divider()

# ── Charts ────────────────────────────────────────────────────────────────────

if latest["charts"]:
    st.markdown("### Charts")

    CHART_LABELS = {
        "_1_wait_bar":    "1 · Avg Waiting Time",
        "_2_wait_cdf":    "2 · Wait Time CDF",
        "_3_eta_quality": "3 · ETA Range Quality",
        "_4_idle_gaps":   "4 · Idle Gaps",
        "_5_by_case_type":"5 · By Case Type",
        "_6_cascade":     "6 · Cascade Effect",
        "_7_conflicts":   "7 · Conflict Detection",
    }

    # Map filenames to labels
    labelled = []
    for chart in latest["charts"]:
        for key, label in CHART_LABELS.items():
            if key in chart.name:
                labelled.append((label, chart))
                break

    labelled.sort(key=lambda x: x[0])

    tabs = st.tabs([lbl for lbl, _ in labelled])
    for tab, (label, chart_path) in zip(tabs, labelled):
        with tab:
            st.image(str(chart_path), use_container_width=True)

    st.divider()

# ── Raw data download ─────────────────────────────────────────────────────────

st.markdown("### Download Results")
dl1, dl2 = st.columns(2)

with dl1:
    if latest["summary"] and latest["summary"].exists():
        with open(latest["summary"], "rb") as f:
            st.download_button(
                "⬇ Summary CSV",
                f.read(),
                file_name=f"eval_summary_{latest['ts']}.csv",
                mime="text/csv",
                use_container_width=True,
            )

with dl2:
    if latest["raw"] and latest["raw"].exists():
        with open(latest["raw"], "rb") as f:
            st.download_button(
                "⬇ Raw Results CSV (all hearings)",
                f.read(),
                file_name=f"eval_raw_{latest['ts']}.csv",
                mime="text/csv",
                use_container_width=True,
            )
