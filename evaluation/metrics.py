"""
Aggregates HearingResult lists into summary metrics for each scenario.
All metric functions operate on plain Python — no DB access required.
"""

import statistics
from dataclasses import dataclass
from evaluation.simulator import HearingResult


@dataclass
class ScenarioMetrics:
    scenario: str
    n_hearings: int
    avg_wait_mins: float
    median_wait_mins: float
    p90_wait_mins: float
    pct_delayed: float            # % hearings with wait > 2 min
    avg_idle_gap_mins: float
    total_idle_mins: float
    eta_range_quality: float      # % actual durations within P25–P75 (0 for baseline)
    overrun_rate: float           # % hearings where actual > scheduled


def compute(results: list[HearingResult], scenario: str) -> ScenarioMetrics:
    if not results:
        return ScenarioMetrics(scenario, 0, 0, 0, 0, 0, 0, 0, 0, 0)

    waits, idles, in_range = [], [], []

    for r in results:
        if scenario == "baseline":
            waits.append(r.baseline_wait_mins)
            idles.append(r.baseline_idle_before_mins)
        elif scenario == "rules":
            waits.append(r.rules_wait_mins)
            idles.append(r.rules_idle_before_mins)
            in_range.append(r.rules_in_range)
        elif scenario == "full":
            waits.append(r.full_wait_mins)
            idles.append(r.full_idle_before_mins)
            in_range.append(r.full_in_range)

    n = len(waits)
    sorted_waits = sorted(waits)
    p90_idx = int(n * 0.90)

    overrun_rate = (
        sum(1 for r in results if r.actual_duration_mins > r.scheduled_duration_mins)
        / n * 100
    )

    return ScenarioMetrics(
        scenario=scenario,
        n_hearings=n,
        avg_wait_mins=round(statistics.mean(waits), 2),
        median_wait_mins=round(statistics.median(waits), 2),
        p90_wait_mins=round(sorted_waits[min(p90_idx, n - 1)], 2),
        pct_delayed=round(sum(1 for w in waits if w > 2) / n * 100, 1),
        avg_idle_gap_mins=round(statistics.mean(idles), 2),
        total_idle_mins=round(sum(idles), 1),
        eta_range_quality=round(
            sum(in_range) / len(in_range) * 100 if in_range else 0.0, 1
        ),
        overrun_rate=round(overrun_rate, 1),
    )


def print_comparison(base: ScenarioMetrics,
                     rules: ScenarioMetrics,
                     full: ScenarioMetrics) -> None:
    header = f"{'Metric':<30} {'Baseline':>10} {'Rules':>10} {'Full':>10}"
    sep = "─" * len(header)
    rows = [
        ("Hearings simulated",    base.n_hearings,         rules.n_hearings,         full.n_hearings,         ""),
        ("Avg wait (min)",        base.avg_wait_mins,      rules.avg_wait_mins,       full.avg_wait_mins,      "↓ better"),
        ("Median wait (min)",     base.median_wait_mins,   rules.median_wait_mins,    full.median_wait_mins,   "↓ better"),
        ("P90 wait (min)",        base.p90_wait_mins,      rules.p90_wait_mins,       full.p90_wait_mins,      "↓ better"),
        ("% hearings delayed",    base.pct_delayed,        rules.pct_delayed,         full.pct_delayed,        "↓ better"),
        ("Avg idle gap (min)",    base.avg_idle_gap_mins,  rules.avg_idle_gap_mins,   full.avg_idle_gap_mins,  "↓ better"),
        ("Total idle (min)",      base.total_idle_mins,    rules.total_idle_mins,     full.total_idle_mins,    "↓ better"),
        ("ETA range quality (%)", "—",                     rules.eta_range_quality,   full.eta_range_quality,  "↑ better"),
        ("Overrun rate (%)",      base.overrun_rate,       rules.overrun_rate,        full.overrun_rate,       ""),
    ]

    print(f"\n{sep}")
    print(header)
    print(sep)
    for label, b, r, f, note in rows:
        print(f"{label:<30} {str(b):>10} {str(r):>10} {str(f):>10}  {note}")
    print(sep)
