"""
Rule-based. Scans the full day's schedule for every lawyer appearing
in the current courtroom's queue and flags any time-overlap with a
hearing in another courtroom.
"""

import json
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy import select

from db.models import Hearing, Case, Lawyer, Courtroom, LawyerConflict, AuditLog
from graph.state import CourtState, ConflictFlag, AuditEvent

AGENT_NAME = "ConflictDetectorAgent"


def run(state: CourtState, db: Session) -> CourtState:
    run_date = datetime.strptime(state["run_date"], "%Y-%m-%d").date()
    conflicts: list[ConflictFlag] = []
    events: list[AuditEvent] = []

    # Collect lawyer IDs involved in this courtroom's queue
    lawyer_ids = _lawyer_ids_in_queue(state["queue"], db)

    for lawyer_id in lawyer_ids:
        lawyer = db.get(Lawyer, lawyer_id)
        if lawyer is None:
            continue

        # All hearings for this lawyer on run_date across all courtrooms
        day_hearings = _hearings_for_lawyer_on_date(db, lawyer_id, run_date)

        # Check every pair for overlap
        for i in range(len(day_hearings)):
            for j in range(i + 1, len(day_hearings)):
                h_a, h_b = day_hearings[i], day_hearings[j]
                if _overlaps(h_a, h_b):
                    conflict = _build_conflict(lawyer, h_a, h_b, db)
                    conflicts.append(conflict)
                    _persist_conflict(db, lawyer_id, h_a, h_b)
                    event = AuditEvent(
                        event_type="CONFLICT_DETECTED",
                        agent_name=AGENT_NAME,
                        entity_type="lawyer",
                        entity_id=lawyer_id,
                        payload={
                            "lawyer_name": lawyer.name,
                            "hearing_a": h_a.id,
                            "hearing_b": h_b.id,
                        },
                        created_at=datetime.now().isoformat(),
                    )
                    _persist_audit(db, event)
                    events.append(event)

    db.commit()

    return {
        **state,
        "conflicts": conflicts,
        "trigger": "conflicts_checked",
        "audit_events": events,
    }


# ── helpers ───────────────────────────────────────────────────────────────────

def _lawyer_ids_in_queue(queue: list, db: Session) -> list[int]:
    ids = []
    for item in queue:
        case = db.execute(
            select(Case).where(Case.id == select(Hearing.case_id)
                               .where(Hearing.id == item["hearing_id"])
                               .scalar_subquery())
        ).scalar_one_or_none()
        if case and case.defense_lawyer_id not in ids:
            ids.append(case.defense_lawyer_id)
    return ids


def _hearings_for_lawyer_on_date(db: Session, lawyer_id: int,
                                  run_date) -> list[Hearing]:
    stmt = (
        select(Hearing)
        .join(Case, Case.id == Hearing.case_id)
        .where(
            Case.defense_lawyer_id == lawyer_id,
            Hearing.status != "cancelled",
        )
    )
    all_hearings = db.execute(stmt).scalars().all()
    return [h for h in all_hearings if h.scheduled_start.date() == run_date]


def _overlaps(h_a: Hearing, h_b: Hearing) -> bool:
    return (
        h_a.scheduled_start < h_b.scheduled_end
        and h_b.scheduled_start < h_a.scheduled_end
    )


def _build_conflict(lawyer: Lawyer, h_a: Hearing,
                    h_b: Hearing, db: Session) -> ConflictFlag:
    cr_a = db.get(Courtroom, h_a.courtroom_id)
    cr_b = db.get(Courtroom, h_b.courtroom_id)
    overlap_start = max(h_a.scheduled_start, h_b.scheduled_start)
    overlap_end = min(h_a.scheduled_end, h_b.scheduled_end)
    return ConflictFlag(
        lawyer_id=lawyer.id,
        lawyer_name=lawyer.name,
        hearing_a_id=h_a.id,
        hearing_b_id=h_b.id,
        courtroom_a=cr_a.name if cr_a else "Unknown",
        courtroom_b=cr_b.name if cr_b else "Unknown",
        overlap_start=overlap_start.isoformat(),
        overlap_end=overlap_end.isoformat(),
    )


def _persist_conflict(db: Session, lawyer_id: int,
                      h_a: Hearing, h_b: Hearing) -> None:
    a_id, b_id = sorted([h_a.id, h_b.id])
    exists = db.execute(
        select(LawyerConflict).where(
            LawyerConflict.lawyer_id == lawyer_id,
            LawyerConflict.hearing_a_id == a_id,
            LawyerConflict.hearing_b_id == b_id,
        )
    ).scalar_one_or_none()
    if exists:
        return
    overlap_start = max(h_a.scheduled_start, h_b.scheduled_start)
    overlap_end = min(h_a.scheduled_end, h_b.scheduled_end)
    db.add(LawyerConflict(
        lawyer_id=lawyer_id,
        hearing_a_id=a_id,
        hearing_b_id=b_id,
        overlap_start=overlap_start,
        overlap_end=overlap_end,
    ))


def _persist_audit(db: Session, event: AuditEvent) -> None:
    db.add(AuditLog(
        event_type=event["event_type"],
        agent_name=event["agent_name"],
        entity_type=event["entity_type"],
        entity_id=event["entity_id"],
        payload=json.dumps(event["payload"]),
    ))
