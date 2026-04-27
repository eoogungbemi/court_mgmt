"""
Rule-based. Flushes any in-memory audit events that were not yet
persisted by individual agents, ensuring the log is always complete.
"""

import json
from datetime import datetime
from sqlalchemy.orm import Session

from db.models import AuditLog
from graph.state import CourtState, AuditEvent

AGENT_NAME = "AuditAgent"


def run(state: CourtState, db: Session) -> CourtState:
    events = state.get("audit_events", [])
    unpersisted = [e for e in events if not e.get("_persisted")]

    for event in unpersisted:
        db.add(AuditLog(
            event_type=event["event_type"],
            agent_name=event["agent_name"],
            entity_type=event.get("entity_type"),
            entity_id=event.get("entity_id"),
            payload=json.dumps(event.get("payload", {})),
        ))

    # Append a CYCLE_COMPLETE marker
    marker = AuditEvent(
        event_type="CYCLE_COMPLETE",
        agent_name=AGENT_NAME,
        entity_type="courtroom",
        entity_id=state["courtroom_id"],
        payload={
            "trigger": state.get("trigger"),
            "queue_length": len(state["queue"]),
            "active_conflicts": len(state.get("conflicts", [])),
        },
        created_at=datetime.now().isoformat(),
    )
    db.add(AuditLog(
        event_type=marker["event_type"],
        agent_name=marker["agent_name"],
        entity_type=marker["entity_type"],
        entity_id=marker["entity_id"],
        payload=json.dumps(marker["payload"]),
    ))
    db.commit()

    return {**state, "audit_events": [marker]}
