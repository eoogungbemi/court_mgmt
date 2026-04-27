"""
Rule-based. Compares wall-clock time against scheduled hearing windows.
Emits OVERRUN_DETECTED when elapsed > estimated * threshold, and
HEARING_COMPLETE when actual_end is set.
"""

import json
from datetime import datetime
from sqlalchemy.orm import Session

from db.models import Hearing, AuditLog
from graph.state import CourtState, HearingSummary, AuditEvent

AGENT_NAME = "QueueMonitorAgent"
OVERRUN_THRESHOLD = 1.15   # flag when elapsed > estimated * 1.15


def run(state: CourtState, db: Session) -> CourtState:
    now = datetime.now()
    queue = state["queue"]
    new_trigger = "nominal"
    trigger_payload: dict = {}
    events: list[AuditEvent] = []

    for item in queue:
        if item["status"] not in ("scheduled", "in_progress"):
            continue

        hearing = db.get(Hearing, item["hearing_id"])
        if hearing is None:
            continue

        # Detect hearing that has been manually marked complete
        if hearing.actual_end is not None and item["status"] != "completed":
            _update_queue_item(queue, hearing.id, "completed")
            event = _make_event("HEARING_COMPLETE", hearing.id, {
                "actual_end": hearing.actual_end.isoformat(),
            })
            _persist_audit(db, event)
            events.append(event)
            new_trigger = "complete"
            trigger_payload = {"hearing_id": hearing.id}
            break

        # Detect overrun on in-progress hearing
        if hearing.actual_start is not None and hearing.actual_end is None:
            elapsed = (now - hearing.actual_start).total_seconds() / 60
            threshold = hearing.estimated_duration_mins * OVERRUN_THRESHOLD
            if elapsed > threshold and item["status"] != "delayed":
                _update_queue_item(queue, hearing.id, "delayed")
                hearing.status = "delayed"
                db.commit()
                event = _make_event("OVERRUN_DETECTED", hearing.id, {
                    "elapsed_mins": round(elapsed, 1),
                    "estimated_mins": hearing.estimated_duration_mins,
                })
                _persist_audit(db, event)
                events.append(event)
                new_trigger = "overrun"
                trigger_payload = {"hearing_id": hearing.id}
                break

    return {
        **state,
        "queue": queue,
        "trigger": new_trigger,
        "trigger_payload": trigger_payload,
        "audit_events": events,
    }


def _update_queue_item(queue: list[HearingSummary],
                       hearing_id: int, status: str) -> None:
    for i, item in enumerate(queue):
        if item["hearing_id"] == hearing_id:
            queue[i] = {**item, "status": status}
            return


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
