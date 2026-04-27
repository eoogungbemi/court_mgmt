"""
Main evaluation runner.

Runs all 20 courtrooms × 5 days under three conditions, aggregates metrics,
runs the conflict detection accuracy test, saves results to CSV, and
triggers chart generation.

Usage:
    PYTHONPATH=. python3 evaluation/run_evaluation.py
    PYTHONPATH=. python3 evaluation/run_evaluation.py --llm   # enable LLM calls
"""

import argparse
import csv
import statistics
from datetime import datetime
from pathlib import Path

from sqlalchemy import select

from db.database import SessionLocal, init_db
from db.models import Courtroom
from evaluation.simulator import simulate_courtroom_day, HearingResult
from evaluation.metrics import compute, ScenarioMetrics, print_comparison
from evaluation import conflict_test

RESULTS_DIR = Path(__file__).parent / "results"
RESULTS_DIR.mkdir(exist_ok=True)


# ── Helpers ───────────────────────────────────────────────────────────────────

def workdays_in_db(db) -> list[str]:
    from db.models import Hearing
    from sqlalchemy import func
    rows = db.execute(
        select(func.date(Hearing.scheduled_start).label("d"))
        .distinct()
        .order_by("d")
    ).scalars().all()
    return [str(r) for r in rows]


def aggregate(all_results: list[HearingResult], scenario: str) -> ScenarioMetrics:
    return compute(all_results, scenario)


def save_csv(all_results: list[HearingResult], path: Path) -> None:
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "hearing_id", "courtroom_inferred", "date",
            "case_type", "hearing_type", "complexity", "position",
            "scheduled_duration_mins", "actual_duration_mins",
            "overrun",
            "baseline_wait_mins",
            "rules_wait_mins",    "rules_p25", "rules_p75", "rules_in_range",
            "full_wait_mins",     "full_p25",  "full_p75",  "full_in_range",
            "full_used_llm",
        ])
        for r in all_results:
            writer.writerow([
                r.hearing_id,
                "",
                r.scheduled_start.strftime("%Y-%m-%d"),
                r.case_type,
                r.hearing_type,
                r.complexity,
                r.position,
                r.scheduled_duration_mins,
                r.actual_duration_mins,
                r.actual_duration_mins > r.scheduled_duration_mins,
                round(r.baseline_wait_mins, 2),
                round(r.rules_wait_mins, 2),
                r.rules_p25, r.rules_p75, r.rules_in_range,
                round(r.full_wait_mins, 2),
                r.full_p25, r.full_p75, r.full_in_range,
                r.full_used_llm,
            ])
    print(f"  Saved raw results → {path}")


def save_summary_csv(metrics: list[ScenarioMetrics], path: Path) -> None:
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=metrics[0].__dataclass_fields__.keys())
        writer.writeheader()
        writer.writerows([m.__dict__ for m in metrics])
    print(f"  Saved summary     → {path}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main(use_llm: bool = False) -> None:
    init_db()
    db = SessionLocal()

    print("=" * 60)
    print("  Court Case Management — Evaluation Suite")
    print(f"  LLM enabled: {use_llm}")
    print("=" * 60)

    # ── Phase 1: Day simulation ───────────────────────────────────────────────
    print("\n[1/3] Running day simulations (20 courtrooms × 5 days)...")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    courtrooms = db.execute(select(Courtroom).order_by(Courtroom.id)).scalars().all()
    work_days  = workdays_in_db(db)

    all_results: list[HearingResult] = []
    total_runs = 0

    for day in work_days:
        day_results: list[HearingResult] = []
        for cr in courtrooms:
            sim = simulate_courtroom_day(
                courtroom_id=cr.id,
                run_date=day,
                db=db,
                seed=42,
                use_llm=use_llm,
            )
            day_results.extend(sim)

        n = len(day_results)
        total_runs += n
        overruns = sum(
            1 for r in day_results
            if r.actual_duration_mins > r.scheduled_duration_mins
        )
        print(f"  {day}: {n} hearings | {overruns} overruns ({overruns/max(n,1)*100:.0f}%)")
        all_results.extend(day_results)

    print(f"\n  Total hearing-simulations: {total_runs}")

    # ── Phase 2: Metrics ──────────────────────────────────────────────────────
    print("\n[2/3] Computing metrics...")

    base_m  = aggregate(all_results, "baseline")
    rules_m = aggregate(all_results, "rules")
    full_m  = aggregate(all_results, "full")

    print_comparison(base_m, rules_m, full_m)

    # Reduction summaries
    wait_reduction_rules = (
        (base_m.avg_wait_mins - rules_m.avg_wait_mins)
        / max(base_m.avg_wait_mins, 0.01) * 100
    )
    wait_reduction_full = (
        (base_m.avg_wait_mins - full_m.avg_wait_mins)
        / max(base_m.avg_wait_mins, 0.01) * 100
    )
    print(f"\n  Avg wait reduction — Rules vs Baseline: {wait_reduction_rules:+.1f}%")
    print(f"  Avg wait reduction — Full  vs Baseline: {wait_reduction_full:+.1f}%")
    print(f"  ETA range quality  — Rules: {rules_m.eta_range_quality}%")
    print(f"  ETA range quality  — Full:  {full_m.eta_range_quality}%")

    # ── Phase 3: Conflict detection test ─────────────────────────────────────
    print("\n[3/3] Conflict detection accuracy test (10 injected conflicts)...")

    try:
        injected = conflict_test.inject_conflicts(db, n_conflicts=10, run_id=ts)
        detection = conflict_test.run_detection(injected, db)
        print(f"  Injected:        {detection.n_injected}")
        print(f"  Detected:        {detection.n_detected}")
        print(f"  False positives: {detection.n_false_positives}")
        print(f"  Precision:       {detection.precision:.3f}")
        print(f"  Recall:          {detection.recall:.3f}")
        print(f"  F1:              {detection.f1:.3f}")
    except Exception as exc:
        print(f"  Conflict test error: {exc}")
        detection = None
    finally:
        conflict_test.cleanup(db)

    # ── Save results ──────────────────────────────────────────────────────────
    save_csv(all_results, RESULTS_DIR / f"raw_{ts}.csv")
    save_summary_csv([base_m, rules_m, full_m], RESULTS_DIR / f"summary_{ts}.csv")

    if detection:
        with open(RESULTS_DIR / f"conflict_{ts}.csv", "w", newline="") as f:
            import csv as _csv
            w = _csv.writer(f)
            w.writerow(["n_injected", "n_detected", "false_positives",
                        "precision", "recall", "f1"])
            w.writerow([detection.n_injected, detection.n_detected,
                        detection.n_false_positives, detection.precision,
                        detection.recall, detection.f1])

    # ── Charts ────────────────────────────────────────────────────────────────
    print("\nGenerating charts...")
    try:
        from evaluation.visualize import generate_all
        generate_all(all_results, base_m, rules_m, full_m, detection, RESULTS_DIR, ts)
    except Exception as exc:
        print(f"  Chart error: {exc}")

    print(f"\nDone. Results saved to {RESULTS_DIR}/")
    db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--llm", action="store_true",
                        help="Enable LLM duration estimates (requires ANTHROPIC_API_KEY)")
    args = parser.parse_args()
    main(use_llm=args.llm)
