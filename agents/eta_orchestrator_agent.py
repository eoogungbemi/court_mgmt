"""
Hybrid. Combines queue position, elapsed time, and duration estimates
to produce the final ETA ranges displayed to users. Recalculates the
entire queue on any queue-altering event.
"""

import json
from datetime import datetime, timedelta
from sqlalchemy.orm import Session

from db.models import Hearing, ETAEstimate as ETAEstimateRow, AuditLog
from graph.state import CourtState, ETAEstimate, HearingSummary, AuditEvent

AGENT_NAME = "ETAOrchestratorAgent"


def run(state: CourtState, db: Session) -> CourtState:
    now = datetime.now()
    queue = state["queue"]
    existing_estimates = state["eta_estimates"]

    updated_estimates: dict[int, ETAEstimate] = {}
    cursor = now  # rolling estimated completion time

    for item in queue:
        if item["status"] == "completed":
            continue

        hearing_id = item["hearing_id"]
        hearing = db.get(Hearing, hearing_id)
        if hearing is None:
            continue

        # If hearing is currently in progress, project its end time
        if hearing.actual_start and not hearing.actual_end:
            p25, p75 = _in_progress_eta(hearing, now, existing_estimates)
            cursor = now + timedelta(minutes=p75)
            estimated_start = hearing.actual_start
        else:
            # Not yet started — base on cursor (rolling end of previous hearing)
            estimated_start = max(cursor, hearing.scheduled_start)
            p25, p75 = _pending_eta(hearing, existing_estimates)
            cursor = estimated_start + timedelta(minutes=p75)

        rationale = _get_rationale(hearing_id, existing_estimates)

        eta = ETAEstimate(
            hearing_id=hearing_id,
            estimated_start=estimated_start.isoformat(),
            p25_mins=p25,
            p75_mins=p75,
            rationale=rationale,
            agent_name=AGENT_NAME,
            generated_at=now.isoformat(),
        )
        updated_estimates[hearing_id] = eta

        # Persist latest ETA
        db.add(ETAEstimateRow(
            hearing_id=hearing_id,
            estimated_start=estimated_start,
            p25_mins=p25,
            p75_mins=p75,
            rationale=rationale,
            agent_name=AGENT_NAME,
        ))

    db.commit()

    event = AuditEvent(
        event_type="ETA_RECALCULATED",
        agent_name=AGENT_NAME,
        entity_type="courtroom",
        entity_id=state["courtroom_id"],
        payload={"hearings_updated": len(updated_estimates)},
        created_at=now.isoformat(),
    )
    db.add(AuditLog(
        event_type=event["event_type"],
        agent_name=event["agent_name"],
        entity_type=event["entity_type"],
        entity_id=event["entity_id"],
        payload=json.dumps(event["payload"]),
    ))
    db.commit()

    return {
        **state,
        "eta_estimates": updated_estimates,
        "trigger": "eta_orchestrated",
        "audit_events": [event],
    }


# ── helpers ───────────────────────────────────────────────────────────────────

def _in_progress_eta(hearing: Hearing, now: datetime,
                     estimates: dict) -> tuple[int, int]:
    elapsed = int((now - hearing.actual_start).total_seconds() / 60)
    remaining_p25 = max(1, hearing.estimated_duration_mins - elapsed - 5)
    remaining_p75 = max(remaining_p25 + 5,
                        hearing.estimated_duration_mins - elapsed + 10)
    prior = estimates.get(hearing.id)
    if prior:
        remaining_p25 = min(remaining_p25, prior["p25_mins"])
        remaining_p75 = min(remaining_p75, prior["p75_mins"])
    return remaining_p25, remaining_p75


def _pending_eta(hearing: Hearing,
                 estimates: dict) -> tuple[int, int]:
    prior = estimates.get(hearing.id)
    if prior:
        return prior["p25_mins"], prior["p75_mins"]
    # No prior estimate — use scheduled duration with ±20% band
    base = hearing.estimated_duration_mins
    return max(5, int(base * 0.8)), int(base * 1.2)


def _get_rationale(hearing_id: int, estimates: dict) -> str:
    prior = estimates.get(hearing_id)
    if prior:
        return prior.get("rationale", "Based on queue position and prior estimates.")
    return "Estimated from scheduled duration."
