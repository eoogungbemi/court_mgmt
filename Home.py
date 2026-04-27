"""
Landing page — role selector and live courtroom status grid.
Run with:  streamlit run Home.py
"""

import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from datetime import date, datetime
import streamlit as st
from sqlalchemy import select, func

from db.database import init_db, SessionLocal
from db.models import Courtroom, Hearing, Judge, Lawyer, Case

st.set_page_config(
    page_title="Court Case Management",
    page_icon="⚖️",
    layout="wide",
    initial_sidebar_state="collapsed",
)

init_db()

# ── Custom CSS ────────────────────────────────────────────────────────────────
st.markdown("""
<style>
    .role-card {
        background: #f8f9fa;
        border: 2px solid #dee2e6;
        border-radius: 12px;
        padding: 28px 20px;
        text-align: center;
        transition: border-color 0.2s;
        min-height: 200px;
    }
    .role-card:hover { border-color: #4c8bf5; }
    .role-icon { font-size: 3rem; margin-bottom: 10px; }
    .role-title { font-size: 1.3rem; font-weight: 700; margin-bottom: 6px; }
    .role-desc  { font-size: 0.9rem; color: #6c757d; margin-bottom: 16px; }
    .stat-box {
        background: #ffffff;
        border: 1px solid #e9ecef;
        border-radius: 8px;
        padding: 16px;
        text-align: center;
    }
    .stat-num  { font-size: 2rem; font-weight: 800; color: #1f77b4; }
    .stat-lbl  { font-size: 0.8rem; color: #6c757d; text-transform: uppercase; }
    .room-chip {
        border-radius: 8px;
        padding: 8px 6px;
        text-align: center;
        font-size: 0.78rem;
        margin-bottom: 4px;
    }
    .in-progress { background: #d4edda; border: 1px solid #28a745; }
    .scheduled   { background: #e8f4fd; border: 1px solid #17a2b8; }
    .idle        { background: #f8f9fa; border: 1px solid #ced4da; color: #6c757d; }
</style>
""", unsafe_allow_html=True)


# ── Header ────────────────────────────────────────────────────────────────────
st.markdown("## ⚖️ AI-Assisted Court Case Management")
st.markdown(
    f"**US Federal Court** · {date.today().strftime('%A, %B %d %Y')} · "
    "Real-time queue management powered by multi-agent AI"
)
st.divider()


# ── Role selector ─────────────────────────────────────────────────────────────
st.markdown("### Select your role to continue")

col_pub, col_clerk, col_lawyer = st.columns(3, gap="large")

with col_pub:
    st.markdown("""
    <div class="role-card">
        <div class="role-icon">📺</div>
        <div class="role-title">Public Queue</div>
        <div class="role-desc">
            View live courtroom queues and estimated hearing start times.
            No login required. Updates every 30 seconds.
        </div>
    </div>
    """, unsafe_allow_html=True)
    st.markdown("")
    st.page_link(
        "pages/1_Public_Queue.py",
        label="View Live Queue →",
        icon="📺",
        use_container_width=True,
    )

with col_clerk:
    st.markdown("""
    <div class="role-card">
        <div class="role-icon">🗂️</div>
        <div class="role-title">Clerk Dashboard</div>
        <div class="role-desc">
            Manage the full docket: check in parties, update hearing status,
            resolve scheduling conflicts, and view audit trails.
        </div>
    </div>
    """, unsafe_allow_html=True)
    st.markdown("")
    st.page_link(
        "pages/2_Clerk_Dashboard.py",
        label="Open Clerk Dashboard →",
        icon="🗂️",
        use_container_width=True,
    )

with col_lawyer:
    st.markdown("""
    <div class="role-card">
        <div class="role-icon">👔</div>
        <div class="role-title">Lawyer Schedule</div>
        <div class="role-desc">
            View your personal docket across all courtrooms, see AI-estimated
            start times with rationale, and check for schedule conflicts.
        </div>
    </div>
    """, unsafe_allow_html=True)
    st.markdown("")
    st.page_link(
        "pages/3_Lawyer_Schedule.py",
        label="View My Schedule →",
        icon="👔",
        use_container_width=True,
    )

st.divider()


# ── Live stats ────────────────────────────────────────────────────────────────
db = SessionLocal()
try:
    today = date.today()

    total_courtrooms = db.execute(
        select(func.count()).select_from(Courtroom)
    ).scalar()

    today_hearings = db.execute(
        select(func.count()).select_from(Hearing)
        .where(func.date(Hearing.scheduled_start) == today.isoformat())
    ).scalar()

    in_progress = db.execute(
        select(func.count()).select_from(Hearing)
        .where(
            func.date(Hearing.scheduled_start) == today.isoformat(),
            Hearing.status == "in_progress",
        )
    ).scalar()

    completed = db.execute(
        select(func.count()).select_from(Hearing)
        .where(
            func.date(Hearing.scheduled_start) == today.isoformat(),
            Hearing.status == "completed",
        )
    ).scalar()

    total_lawyers = db.execute(select(func.count()).select_from(Lawyer)).scalar()

    st.markdown("### Today at a Glance")
    s1, s2, s3, s4, s5 = st.columns(5)

    with s1:
        st.markdown(f"""
        <div class="stat-box">
            <div class="stat-num">{total_courtrooms}</div>
            <div class="stat-lbl">Courtrooms</div>
        </div>""", unsafe_allow_html=True)

    with s2:
        st.markdown(f"""
        <div class="stat-box">
            <div class="stat-num">{today_hearings}</div>
            <div class="stat-lbl">Hearings Today</div>
        </div>""", unsafe_allow_html=True)

    with s3:
        st.markdown(f"""
        <div class="stat-box">
            <div class="stat-num" style="color:#28a745">{in_progress}</div>
            <div class="stat-lbl">In Progress</div>
        </div>""", unsafe_allow_html=True)

    with s4:
        st.markdown(f"""
        <div class="stat-box">
            <div class="stat-num" style="color:#6c757d">{completed}</div>
            <div class="stat-lbl">Completed</div>
        </div>""", unsafe_allow_html=True)

    with s5:
        st.markdown(f"""
        <div class="stat-box">
            <div class="stat-num">{total_lawyers}</div>
            <div class="stat-lbl">Lawyers on Docket</div>
        </div>""", unsafe_allow_html=True)

    st.divider()

    # ── Courtroom status grid ─────────────────────────────────────────────────
    st.markdown("### Courtroom Status — All 20 Rooms")

    courtrooms = db.execute(
        select(Courtroom).order_by(Courtroom.id)
    ).scalars().all()

    cols = st.columns(5)

    for idx, room in enumerate(courtrooms):
        judge = db.get(Judge, room.judge.id) if room.judge else None

        # Count hearings today
        room_count = db.execute(
            select(func.count()).select_from(Hearing)
            .where(
                Hearing.courtroom_id == room.id,
                func.date(Hearing.scheduled_start) == today.isoformat(),
            )
        ).scalar()

        # Find current or next hearing
        current = db.execute(
            select(Hearing)
            .where(
                Hearing.courtroom_id == room.id,
                Hearing.status == "in_progress",
            )
            .limit(1)
        ).scalar_one_or_none()

        next_h = db.execute(
            select(Hearing)
            .where(
                Hearing.courtroom_id == room.id,
                Hearing.status == "scheduled",
                func.date(Hearing.scheduled_start) == today.isoformat(),
            )
            .order_by(Hearing.scheduled_start)
            .limit(1)
        ).scalar_one_or_none()

        if current:
            css_class = "in-progress"
            status_line = "🟢 In Progress"
            detail = current.hearing_type.replace("_", " ").title()
        elif next_h:
            css_class = "scheduled"
            status_line = f"🔵 {next_h.scheduled_start.strftime('%-I:%M %p')}"
            detail = next_h.hearing_type.replace("_", " ").title()
        else:
            css_class = "idle"
            status_line = "⚪ Idle"
            detail = "No hearings"

        judge_name = judge.name if judge else "—"

        with cols[idx % 5]:
            st.markdown(f"""
            <div class="room-chip {css_class}">
                <strong>{room.name}</strong><br>
                <span style="font-size:0.7rem;color:#495057">{judge_name}</span><br>
                {status_line}<br>
                <span style="font-size:0.7rem">{detail}</span><br>
                <span style="font-size:0.7rem;color:#6c757d">{room_count} hearing(s)</span>
            </div>
            """, unsafe_allow_html=True)

finally:
    db.close()

st.divider()
st.caption(
    "AI-Assisted Court Case Management · Multi-Agent System · "
    "Powered by LangGraph + Claude · Prototype for Research Purposes"
)

# Auto-refresh every 60 seconds
st.markdown(
    '<meta http-equiv="refresh" content="60">',
    unsafe_allow_html=True,
)
