from typing import TypedDict, Annotated
from datetime import datetime
import operator


class HearingSummary(TypedDict):
    hearing_id: int
    case_number: str
    case_type: str
    hearing_type: str
    lawyer_name: str
    accused_name: str
    scheduled_start: str       # ISO string
    estimated_duration_mins: int
    status: str
    lawyer_checked_in: bool
    accused_checked_in: bool


class ETAEstimate(TypedDict):
    hearing_id: int
    estimated_start: str       # ISO string
    p25_mins: int
    p75_mins: int
    rationale: str
    agent_name: str
    generated_at: str


class ConflictFlag(TypedDict):
    lawyer_id: int
    lawyer_name: str
    hearing_a_id: int
    hearing_b_id: int
    courtroom_a: str
    courtroom_b: str
    overlap_start: str
    overlap_end: str


class AuditEvent(TypedDict):
    event_type: str
    agent_name: str
    entity_type: str
    entity_id: int | None
    payload: dict
    created_at: str


class CourtState(TypedDict):
    # Which courtroom this graph instance manages
    courtroom_id: int
    courtroom_name: str
    run_date: str                                          # YYYY-MM-DD

    # Ordered queue for the day
    queue: list[HearingSummary]

    # Current ETA estimates keyed by hearing_id
    eta_estimates: dict[int, ETAEstimate]

    # Active conflicts detected
    conflicts: list[ConflictFlag]

    # Append-only audit trail for this run
    audit_events: Annotated[list[AuditEvent], operator.add]

    # Trigger signals consumed by the router
    trigger: str                                           # checkin | overrun | tick | complete
    trigger_payload: dict
