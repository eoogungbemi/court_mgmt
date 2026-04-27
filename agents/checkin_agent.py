"""
Rule-based. Records a party's arrival and marks the hearing ready
when both lawyer and accused have checked in.
"""

import json
from datetime import datetime
from sqlalchemy.orm import Session

from db.models import Hearing, AuditLog
from graph.state import CourtState, AuditEvent


AGENT_NAME = "CheckInAgent"


def run(state: CourtState, db: Session) -> CourtState:
    payload = state["trigger_payload"]
    hearing_id: int = payload["hearing_id"]
    party: str = payload["party"]          # "lawyer" | "accused"

    hearing = db.get(Hearing, hearing_id)
    if hearing is None:
        return _audit(state, "CHECKIN_NOT_FOUND", hearing_id,
                      {"error": f"Hearing {hearing_id} not found"})

    if party == "lawyer":
        hearing.lawyer_checked_in = True
    elif party == "accused":
        hearing.accused_checked_in = True

    db.commit()

    # Reflect check-in in the in-memory queue
    queue = state["queue"]
    updated_queue = []
    for item in queue:
        if item["hearing_id"] == hearing_id:
            item = {
                **item,
                "lawyer_checked_in": hearing.lawyer_checked_in,
                "accused_checked_in": hearing.accused_checked_in,
            }
        updated_queue.append(item)

    event = _make_event("PARTY_CHECKED_IN", hearing_id, {
        "party": party,
        "lawyer_ready": hearing.lawyer_checked_in,
        "accused_ready": hearing.accused_checked_in,
        "all_ready": hearing.lawyer_checked_in and hearing.accused_checked_in,
    })

    _persist_audit(db, event)

    return {
        **state,
        "queue": updated_queue,
        "trigger": "checkin_done",
        "audit_events": [event],
    }


def _make_event(event_type: str, entity_id: int, payload: dict) -> AuditEvent:
    return AuditEvent(
        event_type=event_type,
        agent_name=AGENT_NAME,
        entity_type="hearing",
        entity_id=entity_id,
        payload=payload,
        created_at=datetime.now().isoformat(),
    )


def _persist_audit(db: Session, event: AuditEvent) -> None:
    db.add(AuditLog(
        event_type=event["event_type"],
        agent_name=event["agent_name"],
        entity_type=event["entity_type"],
        entity_id=event["entity_id"],
        payload=json.dumps(event["payload"]),
    ))
    db.commit()


def _audit(state: CourtState, event_type: str,
           entity_id: int, payload: dict) -> CourtState:
    event = _make_event(event_type, entity_id, payload)
    return {**state, "audit_events": [event]}
