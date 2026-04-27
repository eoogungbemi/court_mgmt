import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import date, datetime, timedelta
import streamlit as st
from sqlalchemy import select

from db.database import init_db, SessionLocal
from db.models import Lawyer, Case, Hearing, Courtroom, ETAEstimate, LawyerConflict

st.set_page_config(
    page_title="Lawyer Schedule · Court",
    page_icon="👔",
    layout="centered",
    initial_sidebar_state="collapsed",
)
init_db()

st.sidebar.page_link("Home.py", label="← Back to Home", icon="⚖️")

st.markdown("## 👔 My Court Schedule")
st.page_link("Home.py", label="← Back to Home", icon="⚖️")
st.divider()

db = SessionLocal()
try:
    lawyers = db.execute(select(Lawyer).order_by(Lawyer.name)).scalars().all()
    if not lawyers:
        st.error("No lawyers in database. Run the seed script first.")
        st.stop()

    lawyer_map = {l.id: f"{l.name} ({l.bar_number})" for l in lawyers}

    col_sel, col_date = st.columns([2, 1])
    with col_sel:
        selected_id = st.selectbox(
            "Lawyer",
            options=list(lawyer_map.keys()),
            format_func=lambda x: lawyer_map[x],
        )
    with col_date:
        run_date = st.date_input("Date", value=date.today())

    st.divider()

    # ── Hearings for this lawyer ──────────────────────────────────────────────
    hearings = db.execute(
        select(Hearing)
        .join(Case, Case.id == Hearing.case_id)
        .where(Case.defense_lawyer_id == selected_id)
        .order_by(Hearing.scheduled_start)
    ).scalars().all()

    day_hearings = [
        h for h in hearings
        if h.scheduled_start.date() == run_date
        and h.status != "cancelled"
    ]

    # ── Conflict check ────────────────────────────────────────────────────────
    conflicts = db.execute(
        select(LawyerConflict)
        .where(
            LawyerConflict.lawyer_id == selected_id,
            LawyerConflict.resolved == False,
        )
    ).scalars().all()

    conflict_hearing_ids: set[int] = set()
    for c in conflicts:
        h_a = db.get(Hearing, c.hearing_a_id)
        if h_a and h_a.scheduled_start.date() == run_date:
            conflict_hearing_ids.add(c.hearing_a_id)
            conflict_hearing_ids.add(c.hearing_b_id)

    if conflict_hearing_ids:
        st.error(
            f"⚠️  **{len(conflicts)} schedule conflict(s) today.** "
            "Contact the clerk immediately."
        )

    if not day_hearings:
        st.info(f"No hearings on {run_date.strftime('%A, %B %d %Y')}.")
        st.stop()

    courtroom_ids = set(h.courtroom_id for h in day_hearings)
    st.success(
        f"**{len(day_hearings)} hearing(s)** across "
        f"**{len(courtroom_ids)} courtroom(s)** on "
        f"{run_date.strftime('%A, %B %d %Y')}"
    )

    # ── Schedule cards ────────────────────────────────────────────────────────
    for h in day_hearings:
        case: Case = h.case
        courtroom: Courtroom = db.get(Courtroom, h.courtroom_id)
        is_conflict = h.id in conflict_hearing_ids

        status_badge = {
            "scheduled":   "🔵",
            "in_progress": "🟢",
            "delayed":     "🟠",
            "completed":   "✅",
        }.get(h.status, "⚪")

        border_color = "#dc3545" if is_conflict else (
            "#28a745" if h.status == "in_progress" else "#dee2e6"
        )

        # Latest ETA
        eta_row = db.execute(
            select(ETAEstimate)
            .where(ETAEstimate.hearing_id == h.id)
            .order_by(ETAEstimate.generated_at.desc())
        ).scalar_one_or_none()

        with st.container(border=True):
            top, timing = st.columns([3, 1])

            with top:
                prefix = "⚠️ " if is_conflict else ""
                st.markdown(
                    f"### {prefix}{status_badge} "
                    f"{courtroom.name if courtroom else 'Unknown'}"
                )
                st.markdown(
                    f"**{h.hearing_type.replace('_', ' ').title()}** · "
                    f"`{case.case_number}` · "
                    f"{case.case_type.title()} · "
                    f"Complexity: {case.complexity.title()}"
                )

            with timing:
                st.markdown(
                    f"**{h.scheduled_start.strftime('%-I:%M %p')}**"
                )
                st.caption(f"~{h.estimated_duration_mins} min est.")

            # ETA range
            if eta_row:
                p25_abs = h.scheduled_start + timedelta(minutes=eta_row.p25_mins)
                p75_abs = h.scheduled_start + timedelta(minutes=eta_row.p75_mins)
                st.info(
                    f"📍 **AI Estimated Start:** "
                    f"{p25_abs.strftime('%-I:%M')} – "
                    f"{p75_abs.strftime('%-I:%M %p')}"
                )
                with st.expander("Why this estimate?"):
                    st.write(eta_row.rationale or "No rationale recorded.")
                    st.caption(
                        f"Agent: `{eta_row.agent_name}` · "
                        f"Generated {eta_row.generated_at.strftime('%-I:%M:%S %p')}"
                    )
            else:
                st.caption(
                    f"📍 Scheduled: {h.scheduled_start.strftime('%-I:%M %p')} "
                    f"(no AI estimate yet)"
                )

            # Conflict detail
            if is_conflict:
                for c in conflicts:
                    other_id = (
                        c.hearing_b_id if c.hearing_a_id == h.id
                        else c.hearing_a_id
                    )
                    if other_id not in {h.id, c.hearing_a_id, c.hearing_b_id}:
                        continue
                    other = db.get(Hearing, other_id)
                    other_room = db.get(Courtroom, other.courtroom_id) if other else None
                    if other and other.scheduled_start.date() == run_date:
                        st.warning(
                            f"Conflict: also scheduled in "
                            f"**{other_room.name if other_room else '?'}** "
                            f"at {other.scheduled_start.strftime('%-I:%M %p')} "
                            f"(overlap {c.overlap_start.strftime('%-I:%M')}–"
                            f"{c.overlap_end.strftime('%-I:%M %p')}). "
                            "Contact the clerk."
                        )

            st.caption(
                f"Status: {h.status.replace('_', ' ').title()} · "
                f"{'✓ Checked in' if h.lawyer_checked_in else '⏳ Not checked in'}"
            )

finally:
    db.close()
