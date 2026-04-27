import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import date, datetime, timedelta
import streamlit as st
from sqlalchemy import select

from db.database import init_db, SessionLocal
from db.models import Courtroom, Hearing, Case, ETAEstimate

st.set_page_config(
    page_title="Public Queue · Court",
    page_icon="📺",
    layout="wide",
    initial_sidebar_state="collapsed",
)
init_db()

# ── Refresh interval (persisted in URL so it survives meta-refresh) ───────────
INTERVAL_OPTIONS = {
    "30 seconds":  30,
    "2 minutes":   120,
    "5 minutes":   300,
    "10 minutes":  600,
    "15 minutes":  900,
}

params        = st.query_params
saved_secs    = int(params.get("refresh", 30))
saved_label   = next(
    (k for k, v in INTERVAL_OPTIONS.items() if v == saved_secs),
    "30 seconds",
)

# ── Header row ────────────────────────────────────────────────────────────────
hdr, ctrl = st.columns([3, 1])

with hdr:
    st.markdown("## 📺 Live Courtroom Queue")
    st.caption(
        f"Updated: {datetime.now().strftime('%I:%M:%S %p')} · "
        f"{date.today().strftime('%A, %B %d %Y')}"
    )
    st.page_link("Home.py", label="← Back to Home", icon="⚖️")

with ctrl:
    st.markdown("<div style='padding-top:8px'></div>", unsafe_allow_html=True)
    chosen_label = st.selectbox(
        "Auto-refresh",
        options=list(INTERVAL_OPTIONS.keys()),
        index=list(INTERVAL_OPTIONS.keys()).index(saved_label),
        key="refresh_select",
    )
    chosen_secs = INTERVAL_OPTIONS[chosen_label]

    # Persist to URL whenever the user changes the setting
    if chosen_secs != saved_secs:
        st.query_params["refresh"] = str(chosen_secs)
        st.rerun()

    mins_left = chosen_secs // 60
    secs_left = chosen_secs % 60
    if mins_left:
        next_refresh = f"Next refresh in ~{mins_left}m {secs_left:02d}s" if secs_left else f"Next refresh in ~{mins_left}m"
    else:
        next_refresh = f"Next refresh in ~{chosen_secs}s"
    st.caption(next_refresh)

st.divider()

st.info(
    "ℹ️  Case numbers only are shown on this screen. "
    "For full details, speak to the court clerk.",
    icon=None,
)

STATUS_BADGE = {
    "scheduled":   "🔵",
    "in_progress": "🟢 **NOW IN SESSION**",
    "delayed":     "🟠 DELAYED",
    "completed":   "✅ Completed",
    "cancelled":   "❌ Cancelled",
}


def fmt_eta(hearing: Hearing, db) -> str:
    row = db.execute(
        select(ETAEstimate)
        .where(ETAEstimate.hearing_id == hearing.id)
        .order_by(ETAEstimate.generated_at.desc())
    ).scalar_one_or_none()
    if row:
        p25 = hearing.scheduled_start + timedelta(minutes=row.p25_mins)
        p75 = hearing.scheduled_start + timedelta(minutes=row.p75_mins)
        return f"Est. {p25.strftime('%-I:%M')} – {p75.strftime('%-I:%M %p')}"
    return f"Scheduled {hearing.scheduled_start.strftime('%-I:%M %p')}"


db = SessionLocal()
try:
    today = date.today()
    courtrooms = db.execute(select(Courtroom).order_by(Courtroom.id)).scalars().all()

    cols = st.columns(4, gap="medium")

    for idx, room in enumerate(courtrooms):
        hearings_today = db.execute(
            select(Hearing)
            .where(
                Hearing.courtroom_id == room.id,
                Hearing.status != "cancelled",
            )
            .order_by(Hearing.scheduled_start)
        ).scalars().all()

        day_hearings = [h for h in hearings_today if h.scheduled_start.date() == today]

        with cols[idx % 4]:
            with st.container(border=True):
                judge = room.judge
                st.markdown(f"**{room.name}**")
                st.caption(judge.name if judge else "")

                if not day_hearings:
                    st.caption("No hearings today")
                    continue

                for h in day_hearings[:7]:
                    case: Case = h.case
                    badge = STATUS_BADGE.get(h.status, "⚪")

                    bg = (
                        "#d4edda" if h.status == "in_progress"
                        else "#fff3cd" if h.status == "delayed"
                        else "#ffffff"
                    )
                    st.markdown(
                        f"<div style='background:{bg};border-radius:6px;"
                        f"padding:6px 8px;margin-bottom:4px;font-size:0.82rem'>"
                        f"{badge} <b>{case.case_number}</b><br>"
                        f"<span style='color:#6c757d'>"
                        f"{h.hearing_type.replace('_',' ').title()}</span><br>"
                        f"<span style='color:#495057'>{fmt_eta(h, db)}</span>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )

                if len(day_hearings) > 7:
                    st.caption(f"+ {len(day_hearings) - 7} more hearings")

finally:
    db.close()

st.divider()
st.page_link("Home.py", label="← Back to Home", icon="⚖️")

# Inject the meta refresh using the chosen interval
st.markdown(
    f'<meta http-equiv="refresh" content="{chosen_secs}">',
    unsafe_allow_html=True,
)
