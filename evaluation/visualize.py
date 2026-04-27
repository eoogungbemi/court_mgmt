"""
Dissertation-quality charts. All figures saved as PNG at 150 dpi.
"""

from __future__ import annotations

import statistics
from pathlib import Path
from typing import Optional

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np

from evaluation.simulator import HearingResult
from evaluation.metrics import ScenarioMetrics
from evaluation.conflict_test import DetectionResult

COLOURS = {
    "baseline": "#d62728",   # red
    "rules":    "#1f77b4",   # blue
    "full":     "#2ca02c",   # green
}
LABELS = {
    "baseline": "Baseline (static)",
    "rules":    "Rules-only agents",
    "full":     "Full system (LLM)",
}
DPI = 150


def _save(fig: plt.Figure, path: Path) -> None:
    fig.savefig(path, dpi=DPI, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved chart → {path.name}")


# ── 1. Waiting time comparison (bar + error) ──────────────────────────────────

def chart_wait_comparison(
    all_results: list[HearingResult],
    out: Path,
) -> None:
    scenarios = ["baseline", "rules", "full"]
    fields = {
        "baseline": "baseline_wait_mins",
        "rules":    "rules_wait_mins",
        "full":     "full_wait_mins",
    }

    avgs, stds = [], []
    for s in scenarios:
        vals = [getattr(r, fields[s]) for r in all_results]
        avgs.append(statistics.mean(vals))
        stds.append(statistics.stdev(vals) if len(vals) > 1 else 0)

    fig, ax = plt.subplots(figsize=(7, 4.5))
    x = np.arange(len(scenarios))
    bars = ax.bar(x, avgs, yerr=stds, capsize=6, width=0.5,
                  color=[COLOURS[s] for s in scenarios],
                  error_kw={"elinewidth": 1.5, "ecolor": "black"})

    for bar, avg in zip(bars, avgs):
        ax.text(bar.get_x() + bar.get_width() / 2,
                bar.get_height() + max(stds) * 0.05,
                f"{avg:.1f} min", ha="center", va="bottom", fontsize=9)

    ax.set_xticks(x)
    ax.set_xticklabels([LABELS[s] for s in scenarios])
    ax.set_ylabel("Average waiting time (minutes)")
    ax.set_title("Average Party Waiting Time by Scheduling Condition\n"
                 "(error bars = ±1 SD, all courtrooms, 5 days)")
    ax.set_ylim(0, max(avgs) * 1.3)
    ax.grid(axis="y", linestyle="--", alpha=0.4)
    fig.tight_layout()
    _save(fig, out)


# ── 2. Wait distribution — CDF ────────────────────────────────────────────────

def chart_wait_cdf(all_results: list[HearingResult], out: Path) -> None:
    fields = {
        "baseline": "baseline_wait_mins",
        "rules":    "rules_wait_mins",
        "full":     "full_wait_mins",
    }

    fig, ax = plt.subplots(figsize=(7, 4.5))

    for s, field in fields.items():
        vals = sorted(getattr(r, field) for r in all_results)
        n = len(vals)
        cdf = np.arange(1, n + 1) / n
        ax.plot(vals, cdf, color=COLOURS[s], label=LABELS[s], linewidth=2)

    ax.set_xlabel("Waiting time (minutes)")
    ax.set_ylabel("Cumulative proportion of hearings")
    ax.set_title("CDF of Party Waiting Times\n"
                 "(left = better; steeper rise = more hearings start on time)")
    ax.legend()
    ax.grid(linestyle="--", alpha=0.4)
    ax.set_xlim(left=0)
    ax.set_ylim(0, 1.02)
    fig.tight_layout()
    _save(fig, out)


# ── 3. ETA range quality (P25–P75 coverage) ──────────────────────────────────

def chart_eta_quality(
    rules_m: ScenarioMetrics,
    full_m: ScenarioMetrics,
    out: Path,
) -> None:
    scenarios = ["rules", "full"]
    quality   = [rules_m.eta_range_quality, full_m.eta_range_quality]

    fig, ax = plt.subplots(figsize=(5, 4))
    bars = ax.bar(
        [LABELS[s] for s in scenarios],
        quality,
        color=[COLOURS[s] for s in scenarios],
        width=0.4,
    )
    for bar, q in zip(bars, quality):
        ax.text(bar.get_x() + bar.get_width() / 2,
                bar.get_height() + 0.5,
                f"{q:.1f}%", ha="center", va="bottom", fontweight="bold")

    ax.axhline(50, linestyle="--", color="grey", linewidth=1,
               label="50% reference")
    ax.set_ylabel("% actual durations within P25–P75 estimate")
    ax.set_title("ETA Range Quality\n"
                 "(% hearings where actual duration fell within predicted range)")
    ax.set_ylim(0, 105)
    ax.legend()
    ax.grid(axis="y", linestyle="--", alpha=0.4)
    fig.tight_layout()
    _save(fig, out)


# ── 4. Idle gap comparison ────────────────────────────────────────────────────

def chart_idle_gaps(
    base_m: ScenarioMetrics,
    rules_m: ScenarioMetrics,
    full_m: ScenarioMetrics,
    out: Path,
) -> None:
    labels  = [LABELS[s] for s in ("baseline", "rules", "full")]
    totals  = [base_m.total_idle_mins, rules_m.total_idle_mins, full_m.total_idle_mins]
    colours = [COLOURS[s] for s in ("baseline", "rules", "full")]

    fig, ax = plt.subplots(figsize=(7, 4))
    bars = ax.barh(labels, totals, color=colours)

    for bar, v in zip(bars, totals):
        ax.text(v + max(totals) * 0.01, bar.get_y() + bar.get_height() / 2,
                f"{v:.0f} min", va="center", fontsize=9)

    ax.set_xlabel("Total courtroom idle time across all courtrooms × 5 days (minutes)")
    ax.set_title("Courtroom Idle Gap (Lower = Better Utilisation)")
    ax.grid(axis="x", linestyle="--", alpha=0.4)
    fig.tight_layout()
    _save(fig, out)


# ── 5. Wait time by case type ─────────────────────────────────────────────────

def chart_wait_by_case_type(
    all_results: list[HearingResult],
    out: Path,
) -> None:
    case_types = sorted({r.case_type for r in all_results})
    scenarios  = ["baseline", "rules", "full"]
    fields     = {
        "baseline": "baseline_wait_mins",
        "rules":    "rules_wait_mins",
        "full":     "full_wait_mins",
    }

    x = np.arange(len(case_types))
    width = 0.25
    fig, ax = plt.subplots(figsize=(8, 4.5))

    for i, s in enumerate(scenarios):
        avgs = []
        for ct in case_types:
            vals = [getattr(r, fields[s]) for r in all_results if r.case_type == ct]
            avgs.append(statistics.mean(vals) if vals else 0)
        ax.bar(x + i * width, avgs, width, label=LABELS[s], color=COLOURS[s])

    ax.set_xticks(x + width)
    ax.set_xticklabels([ct.title() for ct in case_types])
    ax.set_ylabel("Average waiting time (minutes)")
    ax.set_title("Average Waiting Time by Case Type and Scheduling Condition")
    ax.legend()
    ax.grid(axis="y", linestyle="--", alpha=0.4)
    fig.tight_layout()
    _save(fig, out)


# ── 6. Conflict detection results ─────────────────────────────────────────────

def chart_conflict_detection(detection: DetectionResult, out: Path) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(9, 4))

    # Left: confusion breakdown
    ax = axes[0]
    tp = detection.n_detected - detection.n_false_positives
    fn = detection.n_injected - tp
    fp = detection.n_false_positives
    labels_pie = ["True Positives", "False Negatives", "False Positives"]
    sizes  = [tp, fn, fp]
    colors = ["#2ca02c", "#d62728", "#ff7f0e"]
    non_zero = [(l, s, c) for l, s, c in zip(labels_pie, sizes, colors) if s > 0]
    if non_zero:
        lbls, szs, clrs = zip(*non_zero)
        ax.pie(szs, labels=lbls, colors=clrs, autopct="%1.0f%%", startangle=90)
    ax.set_title("Conflict Detection Breakdown")

    # Right: precision / recall / F1 gauge
    ax2 = axes[1]
    metrics_bar = [detection.precision, detection.recall, detection.f1]
    metric_lbls = ["Precision", "Recall", "F1"]
    bars = ax2.bar(metric_lbls, metrics_bar,
                   color=["#1f77b4", "#ff7f0e", "#2ca02c"], width=0.4)
    for bar, v in zip(bars, metrics_bar):
        ax2.text(bar.get_x() + bar.get_width() / 2,
                 bar.get_height() + 0.02,
                 f"{v:.3f}", ha="center", va="bottom", fontweight="bold")
    ax2.set_ylim(0, 1.15)
    ax2.set_title(f"Detection Metrics\n(n={detection.n_injected} injected conflicts)")
    ax2.grid(axis="y", linestyle="--", alpha=0.4)

    fig.suptitle("Conflict Detector Agent Accuracy", fontsize=12, fontweight="bold")
    fig.tight_layout()
    _save(fig, out)


# ── 7. Queue cascade — position vs wait ──────────────────────────────────────

def chart_cascade_effect(all_results: list[HearingResult], out: Path) -> None:
    """Shows how waiting time grows as queue position increases (cascade)."""
    max_pos = max(r.position for r in all_results)
    positions = list(range(min(max_pos + 1, 12)))   # cap at 12 for readability

    fields = {
        "baseline": "baseline_wait_mins",
        "rules":    "rules_wait_mins",
        "full":     "full_wait_mins",
    }

    fig, ax = plt.subplots(figsize=(8, 4.5))

    for s, field in fields.items():
        avgs = []
        for pos in positions:
            vals = [getattr(r, field) for r in all_results if r.position == pos]
            avgs.append(statistics.mean(vals) if vals else 0)
        ax.plot(positions, avgs, marker="o", color=COLOURS[s],
                label=LABELS[s], linewidth=2, markersize=5)

    ax.set_xlabel("Queue position (0 = first hearing of the day)")
    ax.set_ylabel("Average waiting time (minutes)")
    ax.set_title("Cascade Effect: Waiting Time by Queue Position\n"
                 "(shows how delays compound through the day)")
    ax.legend()
    ax.grid(linestyle="--", alpha=0.4)
    fig.tight_layout()
    _save(fig, out)


# ── Entry point ───────────────────────────────────────────────────────────────

def generate_all(
    all_results: list[HearingResult],
    base_m: ScenarioMetrics,
    rules_m: ScenarioMetrics,
    full_m: ScenarioMetrics,
    detection: Optional[DetectionResult],
    out_dir: Path,
    ts: str,
) -> None:
    prefix = out_dir / ts

    chart_wait_comparison(all_results,       Path(f"{prefix}_1_wait_bar.png"))
    chart_wait_cdf(all_results,              Path(f"{prefix}_2_wait_cdf.png"))
    chart_eta_quality(rules_m, full_m,       Path(f"{prefix}_3_eta_quality.png"))
    chart_idle_gaps(base_m, rules_m, full_m, Path(f"{prefix}_4_idle_gaps.png"))
    chart_wait_by_case_type(all_results,     Path(f"{prefix}_5_by_case_type.png"))
    chart_cascade_effect(all_results,        Path(f"{prefix}_6_cascade.png"))

    if detection and (detection.n_injected + detection.n_detected) > 0:
        chart_conflict_detection(detection,  Path(f"{prefix}_7_conflicts.png"))
