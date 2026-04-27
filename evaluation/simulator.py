"""
Simulation engine.

For each courtroom-day, samples "actual" hearing durations once (lognormal),
then replays the day under three conditions so comparisons are apples-to-apples:

  BASELINE   — DB-scheduled docket (tight), no adaptation.
               Shows how delays cascade when estimates are optimistic.

  RULES_ONLY — Docket rebuilt using p75 rule estimates as slot widths.
               More slack → less cascading, but more idle time.

  FULL       — Same as RULES_ONLY but slot widths come from LLM p75 estimates.
               Falls back to rules if no API key.

The core tradeoff the evaluation surfaces:
  tight schedule  → low idle time, high waiting when overruns cascade
  loose schedule  → higher idle time, lower waiting (overruns absorbed by slack)
"""

import json
import math
import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session
from sqlalchemy import select

from config import (
    BUFFER_BETWEEN_HEARINGS_MINS,
    HEARING_DURATIONS,
    COMPLEXITY_MULTIPLIER,
    ANTHROPIC_API_KEY,
)
from db.models import Hearing, Case

BUFFER = BUFFER_BETWEEN_HEARINGS_MINS


# ── Duration sampling ─────────────────────────────────────────────────────────

def sample_actual_duration(scheduled_mins: int, rng: random.Random) -> int:
    """
    Lognormal sample centred on scheduled_mins, sigma=0.3.
    ~47 % of hearings will exceed their scheduled duration (overrun).
    ~20 % will exceed 1.2× scheduled (significant overrun).
    Capped at 3× to avoid pathological outliers.
    """
    mu = math.log(max(scheduled_mins, 1))
    actual = rng.lognormvariate(mu, 0.3)
    return max(5, min(int(round(actual)), scheduled_mins * 3))


# ── Per-hearing result ────────────────────────────────────────────────────────

@dataclass
class HearingResult:
    hearing_id: int
    position: int                    # 0-indexed queue position
    case_type: str
    hearing_type: str
    complexity: str
    scheduled_start: datetime        # original DB scheduled time (baseline reference)
    scheduled_duration_mins: int
    actual_duration_mins: int

    # ── Baseline (DB-scheduled docket, cascade actual durations)
    baseline_actual_start: Optional[datetime] = None
    baseline_wait_mins: float = 0.0
    baseline_idle_before_mins: float = 0.0

    # ── Rules-only (p75-buffered docket, cascade actual durations)
    rules_scheduled_start: Optional[datetime] = None   # re-planned start
    rules_actual_start: Optional[datetime] = None
    rules_wait_mins: float = 0.0
    rules_idle_before_mins: float = 0.0
    rules_p25: int = 0
    rules_p75: int = 0
    rules_in_range: bool = False     # actual_duration in [p25, p75]

    # ── Full system (LLM p75-buffered docket, cascade actual durations)
    full_scheduled_start: Optional[datetime] = None    # re-planned start
    full_actual_start: Optional[datetime] = None
    full_wait_mins: float = 0.0
    full_idle_before_mins: float = 0.0
    full_p25: int = 0
    full_p75: int = 0
    full_in_range: bool = False
    full_used_llm: bool = False


# ── Helpers ───────────────────────────────────────────────────────────────────

def _rule_estimate(case_type: str, hearing_type: str,
                   complexity: str) -> tuple[int, int]:
    lo, hi = HEARING_DURATIONS.get(case_type, {}).get(hearing_type, (20, 40))
    mult = COMPLEXITY_MULTIPLIER[complexity]
    p25 = max(5, int(lo * mult))
    p75 = max(p25 + 5, int(hi * mult))
    return p25, p75


def _llm_estimates(hearings: list[Hearing]) -> dict[int, tuple[int, int]]:
    """
    LLM calls for a batch of hearings → {hearing_id: (p25, p75)}.
    Returns empty dict if ANTHROPIC_API_KEY is not set.
    """
    if not ANTHROPIC_API_KEY:
        return {}

    import anthropic

    SYSTEM = (
        "You are a US court scheduling assistant. "
        "Estimate hearing duration. Output only JSON: "
        '{"p25_mins": <int>, "p75_mins": <int>}'
    )
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    results: dict[int, tuple[int, int]] = {}

    for h in hearings:
        try:
            resp = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=64,
                system=SYSTEM,
                messages=[{
                    "role": "user",
                    "content": (
                        f"case_type={h.case.case_type} "
                        f"hearing_type={h.hearing_type} "
                        f"complexity={h.case.complexity}"
                    ),
                }],
            )
            raw = json.loads(resp.content[0].text.strip())
            p25, p75 = int(raw["p25_mins"]), int(raw["p75_mins"])
            if 0 < p25 < p75:
                results[h.id] = (p25, p75)
        except Exception:
            pass

    return results


def _load_hearings(courtroom_id: int, run_date, db: Session) -> list[Hearing]:
    from datetime import datetime as _dt
    if isinstance(run_date, str):
        run_date = _dt.strptime(run_date, "%Y-%m-%d").date()

    rows = db.execute(
        select(Hearing)
        .where(Hearing.courtroom_id == courtroom_id,
               Hearing.status != "cancelled")
        .order_by(Hearing.scheduled_start)
    ).scalars().all()
    return [h for h in rows if h.scheduled_start.date() == run_date]


# ── Main simulation ───────────────────────────────────────────────────────────

def simulate_courtroom_day(
    courtroom_id: int,
    run_date,
    db: Session,
    seed: int = 42,
    use_llm: bool = False,
) -> list[HearingResult]:
    """
    Simulate one courtroom for one day under all three conditions.
    Actual durations are sampled once and shared so comparisons are fair.
    """
    hearings = _load_hearings(courtroom_id, run_date, db)
    if not hearings:
        return []

    rng = random.Random(seed + courtroom_id)
    n = len(hearings)
    actuals = [sample_actual_duration(h.estimated_duration_mins, rng) for h in hearings]

    # Gather p25/p75 estimates for every hearing
    llm_ests = _llm_estimates(hearings) if use_llm else {}

    estimates: list[tuple[int, int, bool]] = []   # (p25, p75, used_llm)
    for h in hearings:
        r_p25, r_p75 = _rule_estimate(h.case.case_type, h.hearing_type,
                                      h.case.complexity)
        if h.id in llm_ests:
            f_p25, f_p75 = llm_ests[h.id]
            estimates.append((f_p25, f_p75, True))
        else:
            estimates.append((r_p25, r_p75, False))

    # ── Pre-plan the three dockets ────────────────────────────────────────────
    # Baseline: use DB scheduled times exactly
    base_sched = [h.scheduled_start for h in hearings]

    # Rules-only: rebuild docket using rule p75 as slot width
    rule_p25 = [_rule_estimate(h.case.case_type, h.hearing_type,
                               h.case.complexity)[0] for h in hearings]
    rule_p75 = [_rule_estimate(h.case.case_type, h.hearing_type,
                               h.case.complexity)[1] for h in hearings]
    rule_sched = [hearings[0].scheduled_start]
    for i in range(1, n):
        rule_sched.append(rule_sched[i - 1] + timedelta(minutes=rule_p75[i - 1] + BUFFER))

    # Full-system: rebuild docket using LLM/fallback p75 as slot width
    full_p25 = [est[0] for est in estimates]
    full_p75 = [est[1] for est in estimates]
    full_used = [est[2] for est in estimates]
    full_sched = [hearings[0].scheduled_start]
    for i in range(1, n):
        full_sched.append(full_sched[i - 1] + timedelta(minutes=full_p75[i - 1] + BUFFER))

    # ── Replay actual durations through each docket ───────────────────────────
    results: list[HearingResult] = []
    base_actual_end = None
    rule_actual_end = None
    full_actual_end = None

    for i, (h, actual) in enumerate(zip(hearings, actuals)):

        # ── Baseline ─────────────────────────────────────────────────────────
        if i == 0:
            b_start = base_sched[0]
            b_idle  = 0.0
        else:
            min_start = base_actual_end + timedelta(minutes=BUFFER)
            b_start = max(base_sched[i], min_start)
            b_idle  = max(0.0, (b_start - min_start).total_seconds() / 60)
        b_wait = max(0.0, (b_start - base_sched[i]).total_seconds() / 60)
        base_actual_end = b_start + timedelta(minutes=actual)

        # ── Rules-only ────────────────────────────────────────────────────────
        if i == 0:
            r_start = rule_sched[0]
            r_idle  = 0.0
        else:
            min_start = rule_actual_end + timedelta(minutes=BUFFER)
            r_start = max(rule_sched[i], min_start)
            r_idle  = max(0.0, (r_start - min_start).total_seconds() / 60)
        r_wait = max(0.0, (r_start - rule_sched[i]).total_seconds() / 60)
        rule_actual_end = r_start + timedelta(minutes=actual)
        r_in_range = rule_p25[i] <= actual <= rule_p75[i]

        # ── Full system ───────────────────────────────────────────────────────
        if i == 0:
            f_start = full_sched[0]
            f_idle  = 0.0
        else:
            min_start = full_actual_end + timedelta(minutes=BUFFER)
            f_start = max(full_sched[i], min_start)
            f_idle  = max(0.0, (f_start - min_start).total_seconds() / 60)
        f_wait = max(0.0, (f_start - full_sched[i]).total_seconds() / 60)
        full_actual_end = f_start + timedelta(minutes=actual)
        f_in_range = full_p25[i] <= actual <= full_p75[i]

        results.append(HearingResult(
            hearing_id=h.id,
            position=i,
            case_type=h.case.case_type,
            hearing_type=h.hearing_type,
            complexity=h.case.complexity,
            scheduled_start=h.scheduled_start,
            scheduled_duration_mins=h.estimated_duration_mins,
            actual_duration_mins=actual,
            baseline_actual_start=b_start,
            baseline_wait_mins=b_wait,
            baseline_idle_before_mins=b_idle,
            rules_scheduled_start=rule_sched[i],
            rules_actual_start=r_start,
            rules_wait_mins=r_wait,
            rules_idle_before_mins=r_idle,
            rules_p25=rule_p25[i],
            rules_p75=rule_p75[i],
            rules_in_range=r_in_range,
            full_scheduled_start=full_sched[i],
            full_actual_start=f_start,
            full_wait_mins=f_wait,
            full_idle_before_mins=f_idle,
            full_p25=full_p25[i],
            full_p75=full_p75[i],
            full_in_range=f_in_range,
            full_used_llm=full_used[i],
        ))

    return results
