import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import date, datetime
import streamlit as st
from sqlalchemy import select

from db.database import init_db, SessionLocal
from db.models import (
    Courtroom, Hearing, Case, Accused, Lawyer,
    LawyerConflict, AuditLog,
)

st.set_page_config(
    page_title="Clerk Dashboard · Court",
    page_icon="🗂️",
    layout="wide",
    initial_sidebar_state="expanded",
)
init_db()


def get_db():
    return SessionLocal()


# ── Sidebar controls ──────────────────────────────────────────────────────────
st.sidebar.markdown("## 🗂️ Clerk Dashboard")
st.sidebar.page_link("Home.py", label="← Back to Home", icon="⚖️")
st.sidebar.divider()

db_s = get_db()
courtrooms = db_s.execute(select(Courtroom).order_by(Courtroom.id)).scalars().all()
db_s.close()

room_map = {r.id: r.name for r in courtrooms}
selected_id = st.sidebar.selectbox(
    "Courtroom",
    options=list(room_map.keys()),
    format_func=lambda x: room_map[x],
)
run_date = st.sidebar.date_input("Date", value=date.today())

if st.sidebar.button("🔄 Refresh", use_container_width=True):
    st.rerun()

st.sidebar.divider()
st.sidebar.caption("Status legend")
st.sidebar.markdown(
    "🟢 In Progress &nbsp; 🔵 Scheduled\n\n"
    "🟠 Delayed &nbsp; ✅ Completed"
)

# ── Page header ───────────────────────────────────────────────────────────────
st.markdown(f"## 🗂️ {room_map[selected_id]}")
st.caption(f"{run_date.strftime('%A, %B %d %Y')}")
st.divider()

# ── Conflict banner ───────────────────────────────────────────────────────────
db = get_db()
try:
    all_conflicts = db.execute(
        select(LawyerConflict).where(LawyerConflict.resolved == False)
    ).scalars().all()

    day_conflicts = [
        c for c in all_conflicts
        if (h := db.get(Hearing, c.hearing_a_id)) and
        h.scheduled_start.date() == run_date
    ]

    if day_conflicts:
        st.error(
            f"⚠️  **{len(day_conflicts)} unresolved lawyer conflict(s) today.** "
            "Expand a hearing below to view details and resolve."
        )
    else:
        st.success("✅ No lawyer conflicts detected for today.")

finally:
    db.close()

# ── Docket ────────────────────────────────────────────────────────────────────
db = get_db()
try:
    hearings = db.execute(
        select(Hearing)
        .where(Hearing.courtroom_id == selected_id)
        .order_by(Hearing.scheduled_start)
    ).scalars().all()
    day_hearings = [h for h in hearings if h.scheduled_start.date() == run_date]

    if not day_hearings:
        st.info("No hearings scheduled for this courtroom on the selected date.")
        st.stop()

    # Summary row
    total = len(day_hearings)
    done  = sum(1 for h in day_hearings if h.status == "completed")
    live  = sum(1 for h in day_hearings if h.status == "in_progress")
    sched = sum(1 for h in day_hearings if h.status == "scheduled")

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total hearings", total)
    m2.metric("Completed",      done)
    m3.metric("In progress",    live)
    m4.metric("Remaining",      sched)
    st.divider()

    for h in day_hearings:
        case: Case   = h.case
        lawyer: Lawyer  = case.defense_lawyer
        accused: Accused = case.accused

        badge = {
            "scheduled":   "🔵",
            "in_progress": "🟢",
            "delayed":     "🟠",
            "completed":   "✅",
            "cancelled":   "❌",
        }.get(h.status, "⚪")

        header = (
            f"{badge} {h.scheduled_start.strftime('%-I:%M %p')} · "
            f"{case.case_number} · "
            f"{h.hearing_type.replace('_', ' ').title()} · "
            f"{case.case_type.title()}"
        )

        with st.expander(header, expanded=(h.status == "in_progress")):
            left, mid, right = st.columns(3)

            with left:
                st.markdown("**Case details**")
                st.write(f"Complexity: {case.complexity.title()}")
                st.write(f"Est. duration: {h.estimated_duration_mins} min")
                if h.actual_start:
                    elapsed = int(
                        (datetime.now() - h.actual_start).total_seconds() / 60
                    )
                    st.write(f"Elapsed: {elapsed} min")

            with mid:
                st.markdown("**Parties**")
                st.write(f"Accused: {accused.name if accused else 'N/A'}")
                st.write(f"Lawyer: {lawyer.name if lawyer else 'N/A'}")
                if lawyer:
                    st.caption(f"Bar # {lawyer.bar_number}")

            with right:
                st.markdown("**Check-in**")
                lawyer_ok  = h.lawyer_checked_in
                accused_ok = h.accused_checked_in

                if not lawyer_ok:
                    if st.button("✅ Lawyer arrived", key=f"l_{h.id}",
                                 use_container_width=True):
                        _db = get_db()
                        _db.get(Hearing, h.id).lawyer_checked_in = True
                        _db.commit(); _db.close()
                        st.rerun()
                else:
                    st.success("Lawyer ✓ checked in")

                if not accused_ok:
                    if st.button("✅ Accused arrived", key=f"a_{h.id}",
                                 use_container_width=True):
                        _db = get_db()
                        _db.get(Hearing, h.id).accused_checked_in = True
                        _db.commit(); _db.close()
                        st.rerun()
                else:
                    st.success("Accused ✓ checked in")

            # Status override
            st.divider()
            status_opts = ["scheduled", "in_progress", "completed",
                           "delayed", "cancelled"]
            sc1, sc2 = st.columns([3, 1])
            with sc1:
                new_status = st.selectbox(
                    "Update status",
                    status_opts,
                    index=status_opts.index(h.status),
                    key=f"s_{h.id}",
                    label_visibility="collapsed",
                )
            with sc2:
                if st.button("Apply", key=f"ap_{h.id}", use_container_width=True):
                    _db = get_db()
                    h2 = _db.get(Hearing, h.id)
                    h2.status = new_status
                    if new_status == "in_progress" and not h2.actual_start:
                        h2.actual_start = datetime.now()
                    if new_status == "completed" and not h2.actual_end:
                        h2.actual_end = datetime.now()
                    _db.commit(); _db.close()
                    st.rerun()

            # Conflict details for this hearing
            db2 = get_db()
            hearing_conflicts = db2.execute(
                select(LawyerConflict).where(
                    LawyerConflict.resolved == False,
                    (LawyerConflict.hearing_a_id == h.id) |
                    (LawyerConflict.hearing_b_id == h.id),
                )
            ).scalars().all()
            db2.close()

            if hearing_conflicts:
                for c in hearing_conflicts:
                    other_id = (
                        c.hearing_b_id if c.hearing_a_id == h.id
                        else c.hearing_a_id
                    )
                    db3 = get_db()
                    other = db3.get(Hearing, other_id)
                    other_room = db3.get(Courtroom, other.courtroom_id) if other else None
                    db3.close()

                    st.warning(
                        f"⚠️ **{lawyer.name}** also scheduled in "
                        f"**{other_room.name if other_room else '?'}** "
                        f"at {other.scheduled_start.strftime('%-I:%M %p') if other else '?'} "
                        f"(overlap {c.overlap_start.strftime('%-I:%M')}–"
                        f"{c.overlap_end.strftime('%-I:%M %p')})"
                    )
                    if st.button(f"Mark resolved", key=f"res_{c.id}",
                                 use_container_width=True):
                        _db = get_db()
                        _db.get(LawyerConflict, c.id).resolved = True
                        _db.commit(); _db.close()
                        st.rerun()

            # Audit trail
            with st.expander("📋 Audit trail"):
                db4 = get_db()
                audit_rows = db4.execute(
                    select(AuditLog)
                    .where(AuditLog.entity_id == h.id,
                           AuditLog.entity_type == "hearing")
                    .order_by(AuditLog.created_at)
                ).scalars().all()
                db4.close()

                if audit_rows:
                    for r in audit_rows:
                        st.caption(
                            f"{r.created_at.strftime('%-I:%M:%S %p')} · "
                            f"**{r.event_type}** · `{r.agent_name}`"
                        )
                else:
                    st.caption("No audit events yet.")

finally:
    db.close()
