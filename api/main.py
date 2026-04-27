"""
FastAPI backend. Exposes REST endpoints for check-in, queue reads,
conflict reads, and audit trail — plus a WebSocket per courtroom for
live queue updates.
"""

from datetime import date, datetime
from typing import Annotated

from fastapi import FastAPI, Depends, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import select

from db.database import get_db, init_db
from db.models import Courtroom, Hearing, Case, Lawyer, LawyerConflict, AuditLog
from graph.workflow import build_graph, make_initial_state

app = FastAPI(title="Court Case Management API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

DbDep = Annotated[Session, Depends(get_db)]

# WebSocket connection registry: courtroom_id → set of active connections
_ws_connections: dict[int, set[WebSocket]] = {}


@app.on_event("startup")
def startup():
    init_db()


# ── Check-in ──────────────────────────────────────────────────────────────────

class CheckInRequest(BaseModel):
    party: str    # "lawyer" | "accused"


@app.post("/checkin/{hearing_id}")
def checkin(hearing_id: int, body: CheckInRequest, db: DbDep):
    hearing = db.get(Hearing, hearing_id)
    if hearing is None:
        raise HTTPException(status_code=404, detail="Hearing not found")
    if body.party not in ("lawyer", "accused"):
        raise HTTPException(status_code=400, detail="party must be lawyer or accused")

    courtroom = db.get(Courtroom, hearing.courtroom_id)
    run_date = hearing.scheduled_start.strftime("%Y-%m-%d")

    state = make_initial_state(
        courtroom_id=courtroom.id,
        courtroom_name=courtroom.name,
        run_date=run_date,
        db=db,
        trigger="checkin",
        trigger_payload={"hearing_id": hearing_id, "party": body.party},
    )
    graph = build_graph(db)
    final_state = graph.invoke(state)

    return {
        "hearing_id": hearing_id,
        "party": body.party,
        "lawyer_checked_in": next(
            (h["lawyer_checked_in"] for h in final_state["queue"]
             if h["hearing_id"] == hearing_id), False,
        ),
        "accused_checked_in": next(
            (h["accused_checked_in"] for h in final_state["queue"]
             if h["hearing_id"] == hearing_id), False,
        ),
        "eta": final_state["eta_estimates"].get(hearing_id),
    }


# ── Queue ─────────────────────────────────────────────────────────────────────

@app.get("/queue/{courtroom_id}")
def get_queue(courtroom_id: int, run_date: str | None = None, db: DbDep = None):
    courtroom = db.get(Courtroom, courtroom_id)
    if courtroom is None:
        raise HTTPException(status_code=404, detail="Courtroom not found")

    target_date = run_date or date.today().strftime("%Y-%m-%d")
    state = make_initial_state(
        courtroom_id=courtroom_id,
        courtroom_name=courtroom.name,
        run_date=target_date,
        db=db,
        trigger="tick",
    )
    graph = build_graph(db)
    final_state = graph.invoke(state)

    return {
        "courtroom": courtroom.name,
        "date": target_date,
        "queue": final_state["queue"],
        "eta_estimates": final_state["eta_estimates"],
        "conflicts": final_state["conflicts"],
    }


# ── Conflicts ─────────────────────────────────────────────────────────────────

@app.get("/conflicts")
def get_conflicts(run_date: str | None = None, db: DbDep = None):
    target = run_date or date.today().strftime("%Y-%m-%d")
    dt = datetime.strptime(target, "%Y-%m-%d")

    rows = db.execute(
        select(LawyerConflict).where(LawyerConflict.resolved == False)
    ).scalars().all()

    results = []
    for row in rows:
        h_a = db.get(Hearing, row.hearing_a_id)
        if h_a and h_a.scheduled_start.date() == dt.date():
            results.append({
                "id": row.id,
                "lawyer_id": row.lawyer_id,
                "hearing_a": row.hearing_a_id,
                "hearing_b": row.hearing_b_id,
                "overlap_start": row.overlap_start.isoformat(),
                "overlap_end": row.overlap_end.isoformat(),
            })
    return results


# ── Hearing detail ────────────────────────────────────────────────────────────

@app.get("/hearing/{hearing_id}")
def get_hearing(hearing_id: int, db: DbDep):
    hearing = db.get(Hearing, hearing_id)
    if hearing is None:
        raise HTTPException(status_code=404, detail="Hearing not found")

    case: Case = hearing.case
    lawyer: Lawyer = case.defense_lawyer
    return {
        "hearing_id": hearing.id,
        "case_number": case.case_number,
        "case_type": case.case_type,
        "complexity": case.complexity,
        "hearing_type": hearing.hearing_type,
        "lawyer": {"id": lawyer.id, "name": lawyer.name, "bar": lawyer.bar_number},
        "accused": {"name": case.accused.name if case.accused else None},
        "scheduled_start": hearing.scheduled_start.isoformat(),
        "scheduled_end": hearing.scheduled_end.isoformat(),
        "status": hearing.status,
        "lawyer_checked_in": hearing.lawyer_checked_in,
        "accused_checked_in": hearing.accused_checked_in,
    }


# ── Clerk overrides ───────────────────────────────────────────────────────────

class StatusUpdate(BaseModel):
    status: str   # in_progress | completed | delayed | cancelled


@app.patch("/hearing/{hearing_id}/status")
def update_hearing_status(hearing_id: int, body: StatusUpdate, db: DbDep):
    hearing = db.get(Hearing, hearing_id)
    if hearing is None:
        raise HTTPException(status_code=404, detail="Hearing not found")

    valid = {"in_progress", "completed", "delayed", "cancelled", "scheduled"}
    if body.status not in valid:
        raise HTTPException(status_code=400, detail=f"status must be one of {valid}")

    hearing.status = body.status
    if body.status == "in_progress" and hearing.actual_start is None:
        hearing.actual_start = datetime.now()
    if body.status == "completed" and hearing.actual_end is None:
        hearing.actual_end = datetime.now()

    db.commit()
    return {"hearing_id": hearing_id, "status": hearing.status}


# ── Audit trail ───────────────────────────────────────────────────────────────

@app.get("/audit/{hearing_id}")
def get_audit(hearing_id: int, db: DbDep):
    rows = db.execute(
        select(AuditLog)
        .where(AuditLog.entity_id == hearing_id,
               AuditLog.entity_type == "hearing")
        .order_by(AuditLog.created_at)
    ).scalars().all()
    return [
        {
            "id": r.id,
            "event_type": r.event_type,
            "agent_name": r.agent_name,
            "payload": r.payload,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]


# ── Courtrooms list ───────────────────────────────────────────────────────────

@app.get("/courtrooms")
def list_courtrooms(db: DbDep):
    rooms = db.execute(select(Courtroom).order_by(Courtroom.id)).scalars().all()
    return [{"id": r.id, "name": r.name, "floor": r.floor} for r in rooms]


# ── WebSocket ─────────────────────────────────────────────────────────────────

@app.websocket("/live/{courtroom_id}")
async def live_queue(websocket: WebSocket, courtroom_id: int):
    await websocket.accept()
    _ws_connections.setdefault(courtroom_id, set()).add(websocket)
    try:
        while True:
            await websocket.receive_text()   # keep-alive ping from client
    except WebSocketDisconnect:
        _ws_connections[courtroom_id].discard(websocket)
