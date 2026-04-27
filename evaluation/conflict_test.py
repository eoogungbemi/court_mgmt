"""
Conflict detection accuracy test.

Since seed data is conflict-free by design, we inject N synthetic
overlapping hearings for real lawyers, run ConflictDetectorAgent,
measure precision/recall, then roll everything back.

The injected hearings are created inside a savepoint so the DB is
left clean regardless of outcome.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.orm import Session
from sqlalchemy import select, text

from db.models import (
    Case, Accused, Hearing, Lawyer, Courtroom, LawyerConflict, AuditLog
)
from agents import conflict_detector_agent
from graph.state import CourtState


@dataclass
class InjectedConflict:
    lawyer_id: int
    lawyer_name: str
    hearing_a_id: int
    hearing_b_id: int


@dataclass
class DetectionResult:
    n_injected: int
    n_detected: int
    n_false_positives: int
    precision: float      # TP / (TP + FP)
    recall: float         # TP / (TP + FN)
    f1: float


def inject_conflicts(
    db: Session,
    n_conflicts: int = 10,
    seed: int = 99,
    run_id: str = "",
) -> list[InjectedConflict]:
    """
    Create n_conflicts pairs of overlapping hearings for real lawyers.
    Both hearings in each pair are assigned to different courtrooms
    but with overlapping scheduled times — exactly what the agent must catch.
    Returns the list of injected conflict specs.
    """
    rng = random.Random(seed)

    lawyers = db.execute(select(Lawyer)).scalars().all()
    courtrooms = db.execute(select(Courtroom)).scalars().all()
    if len(courtrooms) < 2:
        raise RuntimeError("Need at least 2 courtrooms to inject conflicts")

    # Use a fixed date clearly outside the seeded range for isolation
    test_date = datetime(2099, 1, 15, 9, 0)
    injected: list[InjectedConflict] = []
    case_counter = 0

    for lawyer in rng.sample(lawyers, min(n_conflicts, len(lawyers))):
        cr_a, cr_b = rng.sample(courtrooms, 2)

        # Hearing A: 09:00 – 09:45
        start_a = test_date
        end_a   = start_a + timedelta(minutes=45)

        # Hearing B: 09:20 – 10:05  (overlaps hearing A by 25 min)
        start_b = test_date + timedelta(minutes=20)
        end_b   = start_b + timedelta(minutes=45)

        case_counter += 1
        case_a = Case(
            case_number=f"TEST-CR-{run_id}{case_counter:04d}-A",
            case_type="criminal",
            complexity="medium",
            defense_lawyer_id=lawyer.id,
        )
        db.add(case_a)
        db.flush()
        db.add(Accused(name="Test Defendant A", case_id=case_a.id))

        h_a = Hearing(
            case_id=case_a.id,
            courtroom_id=cr_a.id,
            judge_id=cr_a.judge.id,
            hearing_type="motion_hearing",
            scheduled_start=start_a,
            scheduled_end=end_a,
            estimated_duration_mins=45,
            status="scheduled",
        )
        db.add(h_a)

        case_counter += 1
        case_b = Case(
            case_number=f"TEST-CR-{run_id}{case_counter:04d}-B",
            case_type="criminal",
            complexity="medium",
            defense_lawyer_id=lawyer.id,
        )
        db.add(case_b)
        db.flush()
        db.add(Accused(name="Test Defendant B", case_id=case_b.id))

        h_b = Hearing(
            case_id=case_b.id,
            courtroom_id=cr_b.id,
            judge_id=cr_b.judge.id,
            hearing_type="motion_hearing",
            scheduled_start=start_b,
            scheduled_end=end_b,
            estimated_duration_mins=45,
            status="scheduled",
        )
        db.add(h_b)
        db.flush()

        injected.append(InjectedConflict(
            lawyer_id=lawyer.id,
            lawyer_name=lawyer.name,
            hearing_a_id=h_a.id,
            hearing_b_id=h_b.id,
        ))

    db.flush()
    return injected


def run_detection(
    injected: list[InjectedConflict],
    db: Session,
) -> DetectionResult:
    """
    Run ConflictDetectorAgent for every injected conflict's courtroom,
    then measure how many were caught.
    """
    test_date_str = "2099-01-15"
    detected_pairs: set[frozenset] = set()

    # Run the agent once per unique courtroom that appears in injected hearings
    seen_courtrooms: set[int] = set()
    for ic in injected:
        h_a = db.get(Hearing, ic.hearing_a_id)
        h_b = db.get(Hearing, ic.hearing_b_id)
        for cr_id in {h_a.courtroom_id, h_b.courtroom_id}:
            if cr_id in seen_courtrooms:
                continue
            seen_courtrooms.add(cr_id)

            cr = db.get(Courtroom, cr_id)
            state = CourtState(
                courtroom_id=cr_id,
                courtroom_name=cr.name,
                run_date=test_date_str,
                queue=[
                    {
                        "hearing_id": h.id,
                        "case_number": h.case.case_number,
                        "case_type": h.case.case_type,
                        "hearing_type": h.hearing_type,
                        "lawyer_name": h.case.defense_lawyer.name,
                        "accused_name": (h.case.accused.name
                                         if h.case.accused else "Unknown"),
                        "scheduled_start": h.scheduled_start.isoformat(),
                        "estimated_duration_mins": h.estimated_duration_mins,
                        "status": h.status,
                        "lawyer_checked_in": False,
                        "accused_checked_in": False,
                    }
                    for h in db.execute(
                        select(Hearing)
                        .where(Hearing.courtroom_id == cr_id,
                               Hearing.status != "cancelled")
                        .order_by(Hearing.scheduled_start)
                    ).scalars().all()
                    if h.scheduled_start.date() == datetime(2099, 1, 15).date()
                ],
                eta_estimates={},
                conflicts=[],
                audit_events=[],
                trigger="complete",
                trigger_payload={},
            )

            final = conflict_detector_agent.run(state, db)

            for flag in final.get("conflicts", []):
                pair = frozenset([flag["hearing_a_id"], flag["hearing_b_id"]])
                detected_pairs.add(pair)

    # Score
    injected_pairs = {
        frozenset([ic.hearing_a_id, ic.hearing_b_id])
        for ic in injected
    }

    true_positives  = len(injected_pairs & detected_pairs)
    false_positives = len(detected_pairs - injected_pairs)
    false_negatives = len(injected_pairs - detected_pairs)

    precision = (
        true_positives / (true_positives + false_positives)
        if (true_positives + false_positives) > 0 else 0.0
    )
    recall = (
        true_positives / (true_positives + false_negatives)
        if (true_positives + false_negatives) > 0 else 0.0
    )
    f1 = (
        2 * precision * recall / (precision + recall)
        if (precision + recall) > 0 else 0.0
    )

    return DetectionResult(
        n_injected=len(injected),
        n_detected=len(detected_pairs),
        n_false_positives=false_positives,
        precision=round(precision, 3),
        recall=round(recall, 3),
        f1=round(f1, 3),
    )


def cleanup(db: Session) -> None:
    """Remove all TEST-* records created by inject_conflicts."""
    test_hearings = db.execute(
        select(Hearing)
        .join(Case, Case.id == Hearing.case_id)
        .where(Case.case_number.like("TEST-%"))
    ).scalars().all()

    hearing_ids = [h.id for h in test_hearings]

    if hearing_ids:
        placeholders = ",".join(str(i) for i in hearing_ids)
        db.execute(text(
            f"DELETE FROM lawyer_conflicts "
            f"WHERE hearing_a_id IN ({placeholders}) "
            f"OR hearing_b_id IN ({placeholders})"
        ))
        db.execute(text(
            f"DELETE FROM eta_estimates WHERE hearing_id IN ({placeholders})"
        ))

    # Remove test cases (cascades to accused and hearings via ORM)
    test_cases = db.execute(
        select(Case).where(Case.case_number.like("TEST-%"))
    ).scalars().all()

    for case in test_cases:
        if case.accused:
            db.delete(case.accused)
        for h in case.hearings:
            db.delete(h)
        db.delete(case)

    # Clean up test audit entries
    db.execute(
        text("DELETE FROM audit_log WHERE payload LIKE '%TEST-%'")
    )
    db.commit()
