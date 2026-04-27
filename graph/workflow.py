"""
LangGraph workflow for a single courtroom instance.

Trigger → Router → Agent(s) → AuditAgent → END

                  checkin ──► CheckInAgent ──► ETAOrchestrator
                  overrun ──► DurationEstimator ──► ETAOrchestrator
                  tick    ──► QueueMonitor ──► (overrun | complete | nominal)
                  complete ──► ConflictDetector ──► ETAOrchestrator
                              └─────────────────────────────────► AuditAgent ──► END
"""

from functools import partial
from sqlalchemy.orm import Session
from langgraph.graph import StateGraph, END

from graph.state import CourtState
from agents import (
    checkin_agent,
    queue_monitor_agent,
    duration_estimator_agent,
    conflict_detector_agent,
    eta_orchestrator_agent,
    audit_agent,
)


def _wrap(agent_module, db: Session):
    """Bind db session to agent run function."""
    def node(state: CourtState) -> CourtState:
        return agent_module.run(state, db)
    node.__name__ = agent_module.AGENT_NAME
    return node


def _router(state: CourtState) -> str:
    return state.get("trigger", "nominal")


def _monitor_router(state: CourtState) -> str:
    trigger = state.get("trigger", "nominal")
    if trigger == "overrun":
        return "overrun"
    if trigger == "complete":
        return "complete"
    return "nominal"


def build_graph(db: Session) -> StateGraph:
    graph = StateGraph(CourtState)

    # Register nodes
    graph.add_node("CheckInAgent",           _wrap(checkin_agent, db))
    graph.add_node("QueueMonitorAgent",      _wrap(queue_monitor_agent, db))
    graph.add_node("DurationEstimatorAgent", _wrap(duration_estimator_agent, db))
    graph.add_node("ConflictDetectorAgent",  _wrap(conflict_detector_agent, db))
    graph.add_node("ETAOrchestratorAgent",   _wrap(eta_orchestrator_agent, db))
    graph.add_node("AuditAgent",             _wrap(audit_agent, db))

    # Entry conditional: route on trigger type
    graph.set_conditional_entry_point(
        _router,
        {
            "checkin":  "CheckInAgent",
            "tick":     "QueueMonitorAgent",
            "complete": "ConflictDetectorAgent",
            "overrun":  "DurationEstimatorAgent",
            "nominal":  "AuditAgent",
        },
    )

    # CheckIn → ETA recalculation
    graph.add_edge("CheckInAgent", "ETAOrchestratorAgent")

    # QueueMonitor → conditional on what it detected
    graph.add_conditional_edges(
        "QueueMonitorAgent",
        _monitor_router,
        {
            "overrun":  "DurationEstimatorAgent",
            "complete": "ConflictDetectorAgent",
            "nominal":  "AuditAgent",
        },
    )

    # DurationEstimator → ETA recalculation
    graph.add_edge("DurationEstimatorAgent", "ETAOrchestratorAgent")

    # ConflictDetector → ETA recalculation
    graph.add_edge("ConflictDetectorAgent", "ETAOrchestratorAgent")

    # ETA always flows to audit then end
    graph.add_edge("ETAOrchestratorAgent", "AuditAgent")
    graph.add_edge("AuditAgent", END)

    return graph.compile()


def load_queue(courtroom_id: int, run_date: str, db: Session) -> list:
    """Build the initial queue state from DB for a given courtroom and date."""
    from datetime import datetime
    from sqlalchemy import select
    from db.models import Hearing, Case, Accused, Lawyer, Courtroom

    date = datetime.strptime(run_date, "%Y-%m-%d").date()

    hearings = db.execute(
        select(Hearing)
        .where(Hearing.courtroom_id == courtroom_id)
        .order_by(Hearing.scheduled_start)
    ).scalars().all()

    queue = []
    for h in hearings:
        if h.scheduled_start.date() != date:
            continue
        case: Case = h.case
        accused: Accused = case.accused
        lawyer: Lawyer = case.defense_lawyer
        queue.append({
            "hearing_id": h.id,
            "case_number": case.case_number,
            "case_type": case.case_type,
            "hearing_type": h.hearing_type,
            "lawyer_name": lawyer.name if lawyer else "Unknown",
            "accused_name": accused.name if accused else "Unknown",
            "scheduled_start": h.scheduled_start.isoformat(),
            "estimated_duration_mins": h.estimated_duration_mins,
            "status": h.status,
            "lawyer_checked_in": h.lawyer_checked_in,
            "accused_checked_in": h.accused_checked_in,
        })
    return queue


def make_initial_state(courtroom_id: int, courtroom_name: str,
                       run_date: str, db: Session,
                       trigger: str = "tick",
                       trigger_payload: dict | None = None) -> CourtState:
    return CourtState(
        courtroom_id=courtroom_id,
        courtroom_name=courtroom_name,
        run_date=run_date,
        queue=load_queue(courtroom_id, run_date, db),
        eta_estimates={},
        conflicts=[],
        audit_events=[],
        trigger=trigger,
        trigger_payload=trigger_payload or {},
    )
