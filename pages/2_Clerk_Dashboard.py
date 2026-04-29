import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import io
import json
from datetime import date, datetime, time, timedelta
import pandas as pd
import streamlit as st
from sqlalchemy import select, func, or_

from db.database import init_db, SessionLocal
from db.models import (
    Courtroom, Hearing, Case, Accused, Lawyer, Judge,
    LawyerConflict, AuditLog,
)
from config import HEARING_DURATIONS, COMPLEXITY_MULTIPLIER, CLERK_PASSWORD

st.set_page_config(
    page_title="Clerk Dashboard · Court",
    page_icon="🗂️",
    layout="wide",
    initial_sidebar_state="expanded",
)
init_db()

# ── Authentication gate ────────────────────────────────────────────────────────
if not st.session_state.get("clerk_auth"):
    st.markdown("## 🔐 Clerk Login")
    st.caption("This area is restricted to authorised court clerks.")
    with st.form("clerk_login"):
        _pw = st.text_input("Password", type="password")
        if st.form_submit_button("Sign in", type="primary", use_container_width=True):
            if _pw == CLERK_PASSWORD:
                st.session_state["clerk_auth"] = True
                st.rerun()
            else:
                st.error("Incorrect password.")
    st.stop()

PRO_SE_BAR = "PRO-SE"


def get_db():
    return SessionLocal()


# Resolve PRO-SE placeholder ID once per session load
_db_ps = get_db()
_pro_se_rec = _db_ps.execute(
    select(Lawyer).where(Lawyer.bar_number == PRO_SE_BAR)
).scalar_one_or_none()
_db_ps.close()
PRO_SE_ID: int | None = _pro_se_rec.id if _pro_se_rec else None


HEARING_TYPES = {
    "criminal": ["arraignment", "bail_hearing", "preliminary_hearing",
                 "motion_hearing", "plea_hearing", "sentencing", "bench_trial"],
    "civil":    ["status_conference", "motion_hearing", "bench_trial"],
    "traffic":  ["arraignment", "plea_hearing", "bench_trial"],
}


# ── Helpers ────────────────────────────────────────────────────────────────────

def next_case_number(db, case_type: str, courtroom_id: int) -> str:
    prefix = {"criminal": "CR", "civil": "CV", "traffic": "TR"}[case_type]
    year = date.today().year
    pattern = f"{year}-{prefix}-%"
    last = db.execute(
        select(Case.case_number)
        .where(Case.case_number.like(pattern))
        .order_by(Case.id.desc())
    ).scalar_one_or_none()
    seq = 1
    if last:
        try:
            seq = int(last.split("-")[2]) + 1
        except (IndexError, ValueError):
            seq = 1
    return f"{year}-{prefix}-{seq:05d}-CR{courtroom_id}"


def lawyer_is_free(db, lawyer_id: int, start: datetime, end: datetime,
                   exclude_hearing_id: int | None = None) -> bool:
    hearings = db.execute(
        select(Hearing)
        .join(Case, Case.id == Hearing.case_id)
        .where(
            Case.defense_lawyer_id == lawyer_id,
            Hearing.status != "cancelled",
            Hearing.scheduled_start < end,
            Hearing.scheduled_end > start,
        )
    ).scalars().all()
    return len([h for h in hearings if h.id != exclude_hearing_id]) == 0


def estimate_duration(case_type: str, hearing_type: str, complexity: str) -> int:
    lo, hi = HEARING_DURATIONS.get(case_type, {}).get(hearing_type, (20, 40))
    mult = COMPLEXITY_MULTIPLIER.get(complexity, 1.0)
    return max(5, int(((lo + hi) / 2) * mult))


def audit(db, event_type: str, entity_id: int, payload: dict,
          entity_type: str = "hearing") -> None:
    db.add(AuditLog(
        event_type=event_type,
        agent_name="ClerkDashboard",
        entity_type=entity_type,
        entity_id=entity_id,
        payload=json.dumps(payload),
    ))


def mask_phone(phone: str | None) -> str:
    """Mask a phone number for display in brief / semi-public contexts."""
    if not phone:
        return ""
    digits = "".join(c for c in phone if c.isdigit())
    return f"***-***-{digits[-4:]}" if len(digits) >= 4 else "***-****"


def _record_export(courtroom_id: int, export_date: str, filename: str) -> None:
    """Write a DOCKET_EXPORTED audit entry — called via download_button on_click."""
    _db = SessionLocal()
    try:
        _db.add(AuditLog(
            event_type="DOCKET_EXPORTED",
            agent_name="ClerkDashboard",
            entity_type="courtroom",
            entity_id=courtroom_id,
            payload=json.dumps({"date": export_date, "file": filename}),
        ))
        _db.commit()
    finally:
        _db.close()


def _avail_table(all_lawyers, check_start: datetime, check_end: datetime) -> pd.DataFrame:
    """Return a DataFrame showing free/busy status for every lawyer."""
    _db = get_db()
    rows = []
    try:
        for l in all_lawyers:
            free = lawyer_is_free(_db, l.id, check_start, check_end)
            rows.append({"Lawyer": l.name, "Bar #": l.bar_number,
                         "Status": "✅ Free" if free else "🔴 Busy"})
    finally:
        _db.close()
    return pd.DataFrame(rows)


# ── Sidebar ────────────────────────────────────────────────────────────────────
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
if st.sidebar.button("🔓 Sign out", use_container_width=True):
    del st.session_state["clerk_auth"]
    st.rerun()

st.sidebar.divider()
st.sidebar.caption("Status legend")
st.sidebar.markdown(
    "🟢 In Progress &nbsp; 🔵 Scheduled\n\n"
    "🟠 Delayed &nbsp; ✅ Completed"
)

# ── Page header ────────────────────────────────────────────────────────────────
st.markdown(f"## 🗂️ {room_map[selected_id]}")
st.caption(f"{run_date.strftime('%A, %B %d %Y')}")

# ══════════════════════════════════════════════════════════════════════════════
# CASE SEARCH  [blocking gap #3]
# ══════════════════════════════════════════════════════════════════════════════

with st.expander("🔍 Case Search — find by case number, accused, or lawyer", expanded=False):
    search_q = st.text_input(
        "Search",
        placeholder="e.g.  2026-CR-00042   or   Jane Smith   or   counsel name",
        key="case_search_q",
        label_visibility="collapsed",
    )
    if search_q.strip():
        _pat = f"%{search_q.strip()}%"
        _db_sr = get_db()
        try:
            hits = _db_sr.execute(
                select(Case)
                .join(Accused, Accused.case_id == Case.id, isouter=True)
                .join(Lawyer, Lawyer.id == Case.defense_lawyer_id, isouter=True)
                .where(or_(
                    Case.case_number.ilike(_pat),
                    Accused.name.ilike(_pat),
                    Lawyer.name.ilike(_pat),
                ))
                .order_by(Case.id.desc())
            ).scalars().unique().all()

            if not hits:
                st.info("No matching cases found.")
            else:
                st.caption(f"{len(hits)} result(s):")
                for mc in hits:
                    nh = _db_sr.execute(
                        select(Hearing)
                        .where(
                            Hearing.case_id == mc.id,
                            Hearing.status.in_(["scheduled", "delayed", "in_progress"]),
                        )
                        .order_by(Hearing.scheduled_start)
                    ).scalars().first()
                    with st.container(border=True):
                        r1, r2, r3, r4 = st.columns([2, 2, 2, 1])
                        _conf_badge = " 🔒" if mc.is_confidential else ""
                        r1.markdown(f"**{mc.case_number}**{_conf_badge}")
                        _acc_disp = mc.accused.name if mc.accused else "—"
                        _ph_disp  = f" · {mask_phone(mc.accused.phone)}" if mc.accused and mc.accused.phone else ""
                        r2.write(f"{_acc_disp}{_ph_disp}")
                        r3.write(mc.defense_lawyer.name if mc.defense_lawyer else "—")
                        r4.write(f"{'🟢' if mc.status == 'active' else '⬛'} {mc.status.title()}")
                        if nh:
                            nh_room = _db_sr.get(Courtroom, nh.courtroom_id)
                            st.caption(
                                f"Next: **{nh.hearing_type.replace('_', ' ').title()}** · "
                                f"{nh.scheduled_start.strftime('%a %b %d %Y, %-I:%M %p')} · "
                                f"{nh_room.name if nh_room else '?'}"
                            )
                        else:
                            st.caption("No upcoming hearings.")
        finally:
            _db_sr.close()

st.divider()

# ── Conflict banner ────────────────────────────────────────────────────────────
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


# ══════════════════════════════════════════════════════════════════════════════
# CASE MANAGEMENT
# ══════════════════════════════════════════════════════════════════════════════

with st.expander("📁 Case Management — Add · Follow-up · Reschedule · Edit · Remove · Bulk", expanded=False):

    (add_tab, followup_tab, reschedule_tab,
     edit_tab, remove_tab, bulk_tab) = st.tabs([
        "➕  Add Case",
        "🔁  Follow-up Hearing",
        "🕐  Set / Change Time",
        "✏️  Edit Case",
        "🗑️  Remove Case",
        "📤  Bulk Upload",
    ])

    # ── ADD CASE ──────────────────────────────────────────────────────────────
    with add_tab:
        st.markdown("**Add a new case and first hearing to the docket**")
        st.caption("The case number is generated automatically.")

        # Flags outside form so lawyer section can react to pro_se reactively
        _flag_col1, _flag_col2 = st.columns(2)
        with _flag_col1:
            ac_pro_se = st.checkbox(
                "Pro se — defendant is self-represented (no defence counsel required)",
                key="ac_pro_se",
            )
        with _flag_col2:
            ac_confidential = st.checkbox(
                "🔒 Sealed / confidential case — hide from public displays",
                key="ac_confidential",
            )

        _db_acl = get_db()
        _all_lawyers = _db_acl.execute(
            select(Lawyer)
            .where(Lawyer.bar_number != PRO_SE_BAR)
            .order_by(Lawyer.name)
        ).scalars().all()
        _db_acl.close()

        with st.form("add_case_form", clear_on_submit=True):
            a1, a2 = st.columns(2)
            with a1:
                ac_case_type = st.selectbox(
                    "Case type", ["criminal", "civil", "traffic"], format_func=str.title,
                )
                ac_hearing_type = st.selectbox(
                    "Hearing type", HEARING_TYPES[ac_case_type],
                    format_func=lambda s: s.replace("_", " ").title(),
                )
                ac_complexity = st.selectbox(
                    "Complexity", ["low", "medium", "high"], index=1, format_func=str.title,
                )
            with a2:
                ac_accused = st.text_input("Accused full name")
                ac_phone   = st.text_input("Accused phone (optional)", placeholder="e.g. 555-0100")
                ac_date = st.date_input("Hearing date", value=run_date, key="ac_date")
                ac_time = st.time_input("Start time", value=time(9, 0), step=300, key="ac_time")

            if ac_pro_se:
                st.info("Self-represented — no counsel will be assigned.")
                ac_lawyer_id = None
            else:
                lawyer_opts = {l.id: f"{l.name}  ({l.bar_number})" for l in _all_lawyers}
                ac_lawyer_id = st.selectbox(
                    "Defence lawyer",
                    options=list(lawyer_opts.keys()),
                    format_func=lambda x: lawyer_opts[x],
                )

            ac_notes = st.text_area(
                "Clerk notes (optional)",
                placeholder="e.g. Interpreter required · Defendant in custody · Remote hearing",
                height=70,
            )
            ac_submitted = st.form_submit_button(
                "Add to Docket", type="primary", use_container_width=True
            )

        # Availability checker — outside form so it's reactive
        with st.expander("🗓️ Check lawyer availability at a time", expanded=False):
            av1, av2 = st.columns(2)
            with av1:
                chk_date_ac = st.date_input("Date", value=run_date, key="chk_date_ac")
            with av2:
                chk_time_ac = st.time_input("Time", value=time(9, 0), step=300, key="chk_time_ac")
            _chk_s = datetime.combine(chk_date_ac, chk_time_ac)
            st.dataframe(
                _avail_table(_all_lawyers, _chk_s, _chk_s + timedelta(hours=1)),
                use_container_width=True, hide_index=True,
                column_config={"Status": st.column_config.TextColumn(width="small")},
            )

        if ac_submitted:
            if not ac_accused.strip():
                st.error("Accused name is required.")
            elif not ac_pro_se and not ac_lawyer_id:
                st.error("Select a defence lawyer or check Pro Se.")
            else:
                _db = get_db()
                try:
                    _lawyer_id = PRO_SE_ID if ac_pro_se else ac_lawyer_id
                    start_dt = datetime.combine(ac_date, ac_time)
                    dur = estimate_duration(ac_case_type, ac_hearing_type, ac_complexity)
                    end_dt = start_dt + timedelta(minutes=dur)
                    if not ac_pro_se and not lawyer_is_free(_db, _lawyer_id, start_dt, end_dt):
                        if not ac_pro_se:
                            st.warning(
                                f"⚠️ **{lawyer_opts[_lawyer_id].split('  ')[0]}** has a "
                                f"conflict at {start_dt.strftime('%-I:%M %p')}. "
                                "Case added — conflict flagged."
                            )
                    courtroom = _db.get(Courtroom, selected_id)
                    case_num = next_case_number(_db, ac_case_type, selected_id)
                    new_case = Case(
                        case_number=case_num, case_type=ac_case_type,
                        complexity=ac_complexity, status="active",
                        defense_lawyer_id=_lawyer_id,
                        is_confidential=ac_confidential,
                    )
                    _db.add(new_case); _db.flush()
                    _db.add(Accused(
                        name=ac_accused.strip(),
                        case_id=new_case.id,
                        phone=ac_phone.strip() or None,
                    ))
                    new_hearing = Hearing(
                        case_id=new_case.id, courtroom_id=selected_id,
                        judge_id=courtroom.judge.id, hearing_type=ac_hearing_type,
                        scheduled_start=start_dt, scheduled_end=end_dt,
                        estimated_duration_mins=dur, status="scheduled",
                        notes=ac_notes.strip() or None,
                    )
                    _db.add(new_hearing); _db.flush()
                    audit(_db, "HEARING_ADDED", new_hearing.id, {
                        "case_number": case_num, "case_type": ac_case_type,
                        "hearing_type": ac_hearing_type, "start": start_dt.isoformat(),
                        "lawyer_id": _lawyer_id, "pro_se": ac_pro_se,
                    })
                    _db.commit()
                    st.success(
                        f"✅ Case **{case_num}** added — "
                        f"{ac_hearing_type.replace('_', ' ').title()} at "
                        f"{start_dt.strftime('%-I:%M %p')} (est. {dur} min)"
                    )
                    st.rerun()
                except Exception as e:
                    _db.rollback(); st.error(f"Failed to add case: {e}")
                finally:
                    _db.close()

    # ── FOLLOW-UP HEARING  [blocking gap #1] ──────────────────────────────────
    with followup_tab:
        st.markdown("**Schedule a next hearing on an existing case**")
        st.caption(
            "Use this for continuances, next appearances, and follow-up motions. "
            "The case record is reused — no new case number is generated."
        )

        _db_fu = get_db()
        active_cases = _db_fu.execute(
            select(Case).where(Case.status == "active").order_by(Case.case_number)
        ).scalars().all()
        _db_fu.close()

        if not active_cases:
            st.info("No active cases found.")
        else:
            fu_case_opts = {
                c.id: (f"{c.case_number} · "
                       f"{c.accused.name if c.accused else '?'} · "
                       f"{c.case_type.title()}")
                for c in active_cases
            }
            fu_case_id = st.selectbox(
                "Select case",
                options=list(fu_case_opts.keys()),
                format_func=lambda x: fu_case_opts[x],
                key="fu_case_select",
            )
            sel_case = next(c for c in active_cases if c.id == fu_case_id)

            fc1, fc2, fc3 = st.columns(3)
            fc1.metric("Case type", sel_case.case_type.title())
            fc2.metric("Complexity", sel_case.complexity.title())
            fc3.metric("Current lawyer",
                       sel_case.defense_lawyer.name if sel_case.defense_lawyer else "—")

            _db_fh = get_db()
            prior = _db_fh.execute(
                select(Hearing).where(Hearing.case_id == fu_case_id)
                .order_by(Hearing.scheduled_start)
            ).scalars().all()
            _db_fh.close()

            if prior:
                st.caption("Hearing history:")
                _BADGE = {"scheduled": "🔵", "completed": "✅", "cancelled": "❌",
                          "in_progress": "🟢", "delayed": "🟠"}
                for ph in prior:
                    b = _BADGE.get(ph.status, "⚪")
                    st.caption(
                        f"  {b}  {ph.scheduled_start.strftime('%b %d %Y, %-I:%M %p')} · "
                        f"{ph.hearing_type.replace('_', ' ').title()} · {ph.status.title()}"
                    )

            st.divider()

            fu_c1, fu_c2 = st.columns(2)
            with fu_c1:
                fu_date = st.date_input("Hearing date", value=run_date, key="fu_date")
            with fu_c2:
                fu_time = st.time_input("Start time", value=time(9, 0), step=300, key="fu_time")
            fu_start = datetime.combine(fu_date, fu_time)

            fu_pro_se = st.checkbox(
                "Pro se — defendant is self-represented (no counsel required)",
                key="fu_pro_se",
                value=(sel_case.defense_lawyer and
                       sel_case.defense_lawyer.bar_number == PRO_SE_BAR),
            )

            _db_flaw = get_db()
            all_lawyers_fu = _db_flaw.execute(
                select(Lawyer)
                .where(Lawyer.bar_number != PRO_SE_BAR)
                .order_by(Lawyer.name)
            ).scalars().all()
            _db_flaw.close()

            with st.expander("🗓️ Check lawyer availability at this time", expanded=False):
                st.dataframe(
                    _avail_table(all_lawyers_fu, fu_start, fu_start + timedelta(hours=1)),
                    use_container_width=True, hide_index=True,
                    column_config={"Status": st.column_config.TextColumn(width="small")},
                )

            with st.form("followup_form", clear_on_submit=True):
                ff1, ff2 = st.columns(2)
                with ff1:
                    fu_hearing_type = st.selectbox(
                        "Hearing type",
                        HEARING_TYPES[sel_case.case_type],
                        format_func=lambda s: s.replace("_", " ").title(),
                    )
                with ff2:
                    fu_complexity = st.selectbox(
                        "Complexity",
                        ["low", "medium", "high"],
                        index=["low", "medium", "high"].index(sel_case.complexity),
                        format_func=str.title,
                    )
                fu_lawyer_opts = {l.id: f"{l.name}  ({l.bar_number})"
                                  for l in all_lawyers_fu}
                if fu_pro_se:
                    st.info("Self-represented — no counsel will be assigned.")
                    fu_lawyer_id = None
                else:
                    _def_idx = (
                        list(fu_lawyer_opts.keys()).index(sel_case.defense_lawyer_id)
                        if sel_case.defense_lawyer_id in fu_lawyer_opts else 0
                    )
                    fu_lawyer_id = st.selectbox(
                        "Defence lawyer",
                        options=list(fu_lawyer_opts.keys()),
                        index=_def_idx,
                        format_func=lambda x: fu_lawyer_opts[x],
                    )
                fu_notes = st.text_area(
                    "Clerk notes (optional)",
                    placeholder="e.g. Interpreter required · Defendant in custody · Remote hearing",
                    height=70,
                )
                fu_submitted = st.form_submit_button(
                    "Schedule Hearing", type="primary", use_container_width=True
                )

            if fu_submitted:
                _db = get_db()
                try:
                    _fu_lawyer_id = PRO_SE_ID if fu_pro_se else fu_lawyer_id
                    dur = estimate_duration(sel_case.case_type, fu_hearing_type, fu_complexity)
                    end_dt = fu_start + timedelta(minutes=dur)
                    if not fu_pro_se and not lawyer_is_free(_db, _fu_lawyer_id, fu_start, end_dt):
                        st.warning(
                            f"⚠️ **{fu_lawyer_opts[_fu_lawyer_id].split('  ')[0]}** has a "
                            f"conflict at {fu_start.strftime('%-I:%M %p')}. "
                            "Hearing added — conflict flagged."
                        )
                    _cr_id = prior[-1].courtroom_id if prior else selected_id
                    cr_obj = _db.get(Courtroom, _cr_id)
                    new_h = Hearing(
                        case_id=fu_case_id, courtroom_id=_cr_id,
                        judge_id=cr_obj.judge.id, hearing_type=fu_hearing_type,
                        scheduled_start=fu_start, scheduled_end=end_dt,
                        estimated_duration_mins=dur, status="scheduled",
                        notes=fu_notes.strip() or None,
                    )
                    _db.add(new_h); _db.flush()

                    if _fu_lawyer_id != sel_case.defense_lawyer_id:
                        old_lid = sel_case.defense_lawyer_id
                        _db.get(Case, fu_case_id).defense_lawyer_id = _fu_lawyer_id
                        audit(_db, "LAWYER_CHANGED", fu_case_id, {
                            "case_number": sel_case.case_number,
                            "old_lawyer_id": old_lid, "new_lawyer_id": _fu_lawyer_id,
                        }, entity_type="case")

                    audit(_db, "HEARING_ADDED", new_h.id, {
                        "case_number": sel_case.case_number,
                        "case_type": sel_case.case_type,
                        "hearing_type": fu_hearing_type,
                        "start": fu_start.isoformat(),
                        "lawyer_id": _fu_lawyer_id,
                        "pro_se": fu_pro_se,
                        "followup": True,
                    })
                    _db.commit()
                    st.success(
                        f"✅ **{fu_hearing_type.replace('_', ' ').title()}** added to "
                        f"**{sel_case.case_number}** — "
                        f"{fu_start.strftime('%B %d, %Y at %-I:%M %p')} (est. {dur} min)"
                    )
                    st.rerun()
                except Exception as e:
                    _db.rollback(); st.error(f"Failed to schedule hearing: {e}")
                finally:
                    _db.close()

    # ── RESCHEDULE / SET TIME ─────────────────────────────────────────────────
    with reschedule_tab:
        st.markdown("**Change the scheduled start time for a hearing**")
        st.caption(
            "Updates both start and end times. "
            "Checks for lawyer conflicts at the new time."
        )
        _db_r = get_db()
        rs_hearings = _db_r.execute(
            select(Hearing)
            .where(
                Hearing.courtroom_id == selected_id,
                Hearing.status.in_(["scheduled", "delayed"]),
            )
            .order_by(Hearing.scheduled_start)
        ).scalars().all()
        rs_day = [h for h in rs_hearings if h.scheduled_start.date() == run_date]
        _db_r.close()

        if not rs_day:
            st.info("No reschedulable hearings for the selected courtroom and date.")
        else:
            rs_opts = {
                h.id: (
                    f"{h.scheduled_start.strftime('%-I:%M %p')} · "
                    f"{h.case.case_number} · "
                    f"{h.hearing_type.replace('_', ' ').title()}"
                )
                for h in rs_day
            }
            with st.form("reschedule_form", clear_on_submit=False):
                rs_hearing_id = st.selectbox(
                    "Select hearing",
                    options=list(rs_opts.keys()),
                    format_func=lambda x: rs_opts[x],
                )
                rc1, rc2 = st.columns(2)
                with rc1:
                    rs_date = st.date_input("New date", value=run_date, key="rs_date")
                with rc2:
                    rs_time = st.time_input("New start time", value=time(9, 0),
                                            step=300, key="rs_time")
                rs_submitted = st.form_submit_button(
                    "Apply New Time", type="primary", use_container_width=True
                )
            if rs_submitted:
                _db = get_db()
                try:
                    h = _db.get(Hearing, rs_hearing_id)
                    old_start = h.scheduled_start
                    new_start = datetime.combine(rs_date, rs_time)
                    new_end = new_start + timedelta(minutes=h.estimated_duration_mins)
                    if not lawyer_is_free(_db, h.case.defense_lawyer_id, new_start,
                                         new_end, exclude_hearing_id=h.id):
                        st.warning("⚠️ Lawyer has a conflict at the new time. "
                                   "Time updated — conflict flagged.")
                    h.scheduled_start = new_start
                    h.scheduled_end = new_end
                    if h.status == "delayed":
                        h.status = "scheduled"
                    audit(_db, "HEARING_RESCHEDULED", h.id, {
                        "old_start": old_start.isoformat(),
                        "new_start": new_start.isoformat(),
                    })
                    _db.commit()
                    st.success(
                        f"✅ **{h.case.case_number}** rescheduled to "
                        f"{new_start.strftime('%-I:%M %p on %A, %b %d')}"
                    )
                    st.rerun()
                except Exception as e:
                    _db.rollback(); st.error(f"Reschedule failed: {e}")
                finally:
                    _db.close()

    # ── EDIT CASE  [blocking gap #2] ──────────────────────────────────────────
    with edit_tab:
        st.markdown("**Change the defence lawyer or close a case**")

        _db_ec = get_db()
        edit_cases = _db_ec.execute(
            select(Case).where(Case.status == "active").order_by(Case.case_number)
        ).scalars().all()
        _db_ec.close()

        if not edit_cases:
            st.info("No active cases.")
        else:
            ec_opts = {
                c.id: (f"{c.case_number} · "
                       f"{c.accused.name if c.accused else '?'} · "
                       f"{c.case_type.title()}")
                for c in edit_cases
            }
            ec_case_id = st.selectbox(
                "Select case",
                options=list(ec_opts.keys()),
                format_func=lambda x: ec_opts[x],
                key="ec_case_select",
            )
            ec_case = next(c for c in edit_cases if c.id == ec_case_id)

            e1, e2, e3 = st.columns(3)
            e1.metric("Case number", ec_case.case_number)
            e2.metric("Current lawyer",
                      ec_case.defense_lawyer.name if ec_case.defense_lawyer else "—")
            e3.metric("Status", ec_case.status.title())

            st.divider()
            st.markdown("**Change defence lawyer**")

            _db_eu = get_db()
            upcoming_ec = _db_eu.execute(
                select(Hearing)
                .where(
                    Hearing.case_id == ec_case_id,
                    Hearing.status.in_(["scheduled", "delayed"]),
                    Hearing.scheduled_start >= datetime.now(),
                )
                .order_by(Hearing.scheduled_start)
            ).scalars().all()
            _db_eu.close()

            if upcoming_ec:
                st.caption(
                    f"This case has **{len(upcoming_ec)}** upcoming hearing(s). "
                    "The new lawyer will be checked for conflicts at those times."
                )

            _db_ecl = get_db()
            all_lawyers_ec = _db_ecl.execute(
                select(Lawyer)
                .where(Lawyer.bar_number != PRO_SE_BAR)
                .order_by(Lawyer.name)
            ).scalars().all()
            _db_ecl.close()
            # Build opts — prepend Pro Se so clerk can switch to/from it
            ec_lawyer_opts = {}
            if PRO_SE_ID:
                ec_lawyer_opts[PRO_SE_ID] = "Self-Represented (Pro Se)"
            ec_lawyer_opts.update({l.id: f"{l.name}  ({l.bar_number})"
                                   for l in all_lawyers_ec})

            with st.form("edit_lawyer_form"):
                _ec_def = (
                    list(ec_lawyer_opts.keys()).index(ec_case.defense_lawyer_id)
                    if ec_case.defense_lawyer_id in ec_lawyer_opts else 0
                )
                ec_new_lawyer_id = st.selectbox(
                    "New defence lawyer",
                    options=list(ec_lawyer_opts.keys()),
                    index=_ec_def,
                    format_func=lambda x: ec_lawyer_opts[x],
                )
                ec_submitted = st.form_submit_button(
                    "Update Lawyer", type="primary", use_container_width=True
                )

            if ec_submitted:
                if ec_new_lawyer_id == ec_case.defense_lawyer_id:
                    st.info("No change — same lawyer selected.")
                else:
                    _db = get_db()
                    try:
                        conflicts = [
                            uh for uh in upcoming_ec
                            if not lawyer_is_free(_db, ec_new_lawyer_id,
                                                  uh.scheduled_start, uh.scheduled_end)
                        ]
                        case_obj = _db.get(Case, ec_case_id)
                        old_lid = case_obj.defense_lawyer_id
                        case_obj.defense_lawyer_id = ec_new_lawyer_id
                        audit(_db, "LAWYER_CHANGED", ec_case_id, {
                            "case_number": ec_case.case_number,
                            "old_lawyer_id": old_lid,
                            "new_lawyer_id": ec_new_lawyer_id,
                        }, entity_type="case")
                        _db.commit()
                        new_name = ec_lawyer_opts[ec_new_lawyer_id].split("  ")[0]
                        if conflicts:
                            st.warning(
                                f"✅ Lawyer updated to **{new_name}** but "
                                f"**{len(conflicts)} conflict(s)** detected "
                                "at upcoming hearing times."
                            )
                        else:
                            st.success(f"✅ Defence lawyer updated to **{new_name}**.")
                        st.rerun()
                    except Exception as e:
                        _db.rollback(); st.error(f"Update failed: {e}")
                    finally:
                        _db.close()

            st.divider()
            st.markdown("**Confidentiality**")
            _is_conf_now = ec_case.is_confidential
            _conf_label = (
                "🔒 This case is currently **sealed / confidential**."
                if _is_conf_now
                else "This case is currently **public**."
            )
            st.caption(_conf_label)
            if st.button(
                "🔒 Seal case" if not _is_conf_now else "🔓 Unseal case",
                key="ec_conf_toggle",
                use_container_width=True,
            ):
                _db = get_db()
                try:
                    _c = _db.get(Case, ec_case_id)
                    _c.is_confidential = not _is_conf_now
                    audit(_db, "CASE_CONFIDENTIALITY_CHANGED", ec_case_id, {
                        "case_number": ec_case.case_number,
                        "is_confidential": not _is_conf_now,
                    }, entity_type="case")
                    _db.commit()
                    st.rerun()
                except Exception as e:
                    _db.rollback(); st.error(f"Update failed: {e}")
                finally:
                    _db.close()

            st.divider()
            st.markdown("**Manage defendants**")
            st.caption("Add co-defendants or remove a defendant from this case.")

            _db_codef = get_db()
            all_accused_ec = _db_codef.execute(
                select(Accused).where(Accused.case_id == ec_case_id).order_by(Accused.id)
            ).scalars().all()
            _db_codef.close()

            for _acc in all_accused_ec:
                _ac1, _ac2, _ac3 = st.columns([3, 2, 1])
                _ac1.write(f"**{_acc.name}**")
                _ac2.caption(_acc.phone or "no phone")
                if len(all_accused_ec) > 1:
                    if _ac3.button("Remove", key=f"rm_acc_{_acc.id}", use_container_width=True):
                        _db = get_db()
                        try:
                            _db.delete(_db.get(Accused, _acc.id))
                            audit(_db, "DEFENDANT_REMOVED", ec_case_id, {
                                "case_number": ec_case.case_number,
                                "accused_name": _acc.name,
                            }, entity_type="case")
                            _db.commit(); st.rerun()
                        except Exception as e:
                            _db.rollback(); st.error(f"Remove failed: {e}")
                        finally:
                            _db.close()
                else:
                    _ac3.caption("primary")

            with st.form("add_codef_form", clear_on_submit=True):
                cd1, cd2 = st.columns(2)
                with cd1:
                    cd_name = st.text_input("Co-defendant name")
                with cd2:
                    cd_phone = st.text_input("Phone (optional)")
                if st.form_submit_button("➕ Add defendant", use_container_width=True):
                    if not cd_name.strip():
                        st.error("Name is required.")
                    else:
                        _db = get_db()
                        try:
                            _db.add(Accused(
                                name=cd_name.strip(),
                                case_id=ec_case_id,
                                phone=cd_phone.strip() or None,
                            ))
                            audit(_db, "DEFENDANT_ADDED", ec_case_id, {
                                "case_number": ec_case.case_number,
                                "accused_name": cd_name.strip(),
                            }, entity_type="case")
                            _db.commit()
                            st.success(f"✅ **{cd_name.strip()}** added as co-defendant.")
                            st.rerun()
                        except Exception as e:
                            _db.rollback(); st.error(f"Add failed: {e}")
                        finally:
                            _db.close()

            st.divider()
            st.markdown("**Close case**")
            st.caption(
                "Mark as concluded. All hearing records and the audit trail are preserved."
            )
            close_confirm = st.checkbox("Confirm: close this case", key="ec_close_confirm")
            if st.button("Close Case", disabled=not close_confirm,
                         key="ec_close_btn", use_container_width=True):
                _db = get_db()
                try:
                    _db.get(Case, ec_case_id).status = "closed"
                    audit(_db, "CASE_CLOSED", ec_case_id,
                          {"case_number": ec_case.case_number}, entity_type="case")
                    _db.commit()
                    st.success(f"✅ Case **{ec_case.case_number}** closed.")
                    st.rerun()
                except Exception as e:
                    _db.rollback(); st.error(f"Failed to close case: {e}")
                finally:
                    _db.close()

    # ── REMOVE CASE ───────────────────────────────────────────────────────────
    with remove_tab:
        st.markdown("**Cancel a hearing or remove a case from the docket**")
        st.caption(
            "Cancelling keeps the record and audit trail intact. "
            "Removing permanently deletes the case and all its hearings. "
            "Shows all active hearings for this courtroom across all dates."
        )
        _db_rm = get_db()
        removable = _db_rm.execute(
            select(Hearing)
            .where(
                Hearing.courtroom_id == selected_id,
                Hearing.status != "cancelled",
            )
            .order_by(Hearing.scheduled_start)
        ).scalars().all()
        _db_rm.close()

        if not removable:
            st.info("No active hearings for this courtroom.")
        else:
            rm_opts = {
                h.id: (
                    f"{h.scheduled_start.strftime('%b %d, %-I:%M %p')} · "
                    f"{h.case.case_number} · "
                    f"{h.hearing_type.replace('_', ' ').title()}"
                )
                for h in removable
            }
            rm_hearing_id = st.selectbox(
                "Select hearing",
                options=list(rm_opts.keys()),
                format_func=lambda x: rm_opts[x],
                key="rm_select",
            )
            rm_action = st.radio(
                "Action",
                ["Cancel hearing only", "Remove case entirely"],
                horizontal=True, key="rm_action",
            )
            st.warning(
                "**Cancel hearing** sets status to Cancelled — record is kept. \n\n"
                "**Remove case** permanently deletes the case, accused, and all "
                "its hearings. This cannot be undone."
            )
            confirm = st.checkbox("I confirm this action", key="rm_confirm")
            if st.button("Apply", type="primary", disabled=not confirm,
                         key="rm_apply", use_container_width=True):
                _db = get_db()
                try:
                    h = _db.get(Hearing, rm_hearing_id)
                    case_num = h.case.case_number
                    if rm_action == "Cancel hearing only":
                        h.status = "cancelled"
                        audit(_db, "HEARING_CANCELLED", h.id, {
                            "case_number": case_num, "reason": "Cancelled by clerk",
                        })
                        _db.commit()
                        st.success(f"✅ Hearing for **{case_num}** cancelled.")
                    else:
                        from sqlalchemy import text
                        case_obj = h.case
                        hearing_ids = [hh.id for hh in case_obj.hearings]
                        if hearing_ids:
                            ph = ",".join(str(i) for i in hearing_ids)
                            _db.execute(text(
                                f"DELETE FROM lawyer_conflicts "
                                f"WHERE hearing_a_id IN ({ph}) OR hearing_b_id IN ({ph})"
                            ))
                            _db.execute(text(
                                f"DELETE FROM eta_estimates WHERE hearing_id IN ({ph})"
                            ))
                        audit(_db, "CASE_REMOVED", h.id, {
                            "case_number": case_num, "case_type": case_obj.case_type,
                        })
                        _db.flush()
                        for hh in list(case_obj.hearings):
                            _db.delete(hh)
                        if case_obj.accused:
                            _db.delete(case_obj.accused)
                        _db.delete(case_obj)
                        _db.commit()
                        st.success(f"✅ Case **{case_num}** and all its hearings removed.")
                    st.rerun()
                except Exception as e:
                    _db.rollback(); st.error(f"Action failed: {e}")
                finally:
                    _db.close()

    # ── BULK UPLOAD ───────────────────────────────────────────────────────────
    with bulk_tab:
        st.markdown("**Upload a CSV or Excel file to add multiple cases at once**")
        st.caption(
            "All rows are assigned to the courtroom selected in the sidebar. "
            "Rows with validation errors are skipped — valid rows are committed together."
        )
        _TEMPLATE_COLS = [
            "accused_name", "case_type", "hearing_type",
            "complexity", "hearing_date", "start_time", "lawyer_bar_number",
        ]
        _TEMPLATE_ROWS = [
            ["Jane Smith",  "criminal", "arraignment",       "low",    "2026-04-30", "09:00", "BAR-001"],
            ["John Doe",    "civil",    "status_conference", "medium", "2026-04-30", "10:00", "BAR-002"],
            ["Alice Brown", "traffic",  "plea_hearing",      "low",    "2026-04-30", "11:00", "BAR-003"],
        ]
        _tmpl_buf = io.BytesIO()
        pd.DataFrame(_TEMPLATE_ROWS, columns=_TEMPLATE_COLS).to_csv(_tmpl_buf, index=False)
        st.download_button(
            "⬇️  Download template CSV",
            data=_tmpl_buf.getvalue(),
            file_name="case_upload_template.csv",
            mime="text/csv",
        )

        uploaded = st.file_uploader(
            "Choose a CSV or Excel file", type=["csv", "xlsx"], key="bulk_uploader",
        )

        if uploaded is not None:
            try:
                raw_df = (pd.read_excel(uploaded, dtype=str)
                          if uploaded.name.endswith(".xlsx")
                          else pd.read_csv(uploaded, dtype=str))
            except Exception as e:
                st.error(f"Could not parse file: {e}")
                raw_df = None

            if raw_df is not None:
                raw_df.columns = [c.strip().lower() for c in raw_df.columns]
                missing_cols = [c for c in _TEMPLATE_COLS if c not in raw_df.columns]
                if missing_cols:
                    st.error(
                        f"Missing column(s): **{', '.join(missing_cols)}**. "
                        "Download the template above."
                    )
                else:
                    raw_df = raw_df[_TEMPLATE_COLS].dropna(how="all").reset_index(drop=True)

                    _db_bl = get_db()
                    all_lawyers_bulk = _db_bl.execute(
                        select(Lawyer).order_by(Lawyer.name)
                    ).scalars().all()
                    _db_bl.close()
                    lawyers_by_bar = {l.bar_number: l for l in all_lawyers_bulk}

                    preview_rows = []
                    _db_val = get_db()
                    try:
                        for idx, row in raw_df.iterrows():
                            def _g(col):
                                v = row.get(col, "")
                                return "" if pd.isna(v) else str(v).strip()

                            accused_name = _g("accused_name")
                            case_type    = _g("case_type").lower()
                            hearing_type = _g("hearing_type").lower()
                            complexity   = _g("complexity").lower()
                            date_str     = _g("hearing_date")
                            time_str     = _g("start_time")
                            bar_num      = _g("lawyer_bar_number")

                            errors, warnings = [], []
                            if not accused_name:
                                errors.append("accused_name required")
                            if case_type not in HEARING_TYPES:
                                errors.append(f"invalid case_type '{case_type}'")
                            elif hearing_type not in HEARING_TYPES[case_type]:
                                errors.append(
                                    f"'{hearing_type}' invalid for {case_type}; "
                                    f"valid: {', '.join(HEARING_TYPES[case_type])}"
                                )
                            if complexity not in ("low", "medium", "high"):
                                errors.append(f"invalid complexity '{complexity}'")
                            h_date, h_time = None, None
                            try:
                                h_date = pd.to_datetime(date_str).date()
                            except Exception:
                                errors.append(f"bad hearing_date '{date_str}'")
                            try:
                                h_time = datetime.strptime(time_str, "%H:%M").time()
                            except Exception:
                                errors.append(f"bad start_time '{time_str}' (use HH:MM)")
                            lawyer = lawyers_by_bar.get(bar_num)
                            if not lawyer:
                                errors.append(f"no lawyer with bar # '{bar_num}'")

                            if not errors and h_date and h_time and lawyer:
                                s_dt = datetime.combine(h_date, h_time)
                                dur = estimate_duration(case_type, hearing_type, complexity)
                                if not lawyer_is_free(_db_val, lawyer.id, s_dt,
                                                      s_dt + timedelta(minutes=dur)):
                                    warnings.append(
                                        f"{lawyer.name} has a conflict at "
                                        f"{s_dt.strftime('%-I:%M %p')}"
                                    )

                            status = ("❌ Error" if errors
                                      else "⚠️ Conflict" if warnings
                                      else "✅ Ready")
                            preview_rows.append({
                                "Row": idx + 1, "Status": status,
                                "Accused": accused_name,
                                "Case type": case_type.title(),
                                "Hearing type": hearing_type.replace("_", " ").title(),
                                "Date": date_str, "Time": time_str,
                                "Lawyer bar #": bar_num,
                                "Notes": "; ".join(errors + warnings),
                            })
                    finally:
                        _db_val.close()

                    n_ready    = sum(1 for r in preview_rows if r["Status"] == "✅ Ready")
                    n_conflict = sum(1 for r in preview_rows if r["Status"] == "⚠️ Conflict")
                    n_error    = sum(1 for r in preview_rows if r["Status"] == "❌ Error")
                    n_commit   = n_ready + n_conflict

                    pc1, pc2, pc3 = st.columns(3)
                    pc1.metric("Ready", n_ready)
                    pc2.metric("Has conflict (will commit)", n_conflict)
                    pc3.metric("Errors (skipped)", n_error)

                    st.dataframe(
                        pd.DataFrame(preview_rows),
                        use_container_width=True, hide_index=True,
                        column_config={
                            "Status": st.column_config.TextColumn(width="small"),
                            "Notes":  st.column_config.TextColumn(width="large"),
                        },
                    )

                    if n_commit == 0:
                        st.error("No valid rows to commit. Fix the errors and re-upload.")
                    else:
                        st.info(
                            f"**{n_commit} row(s)** will be added to "
                            f"**{room_map[selected_id]}**. "
                            f"{n_error} error row(s) skipped."
                        )
                        bulk_confirm = st.checkbox(
                            f"Confirm: add {n_commit} case(s) to the docket",
                            key="bulk_confirm",
                        )
                        if st.button(
                            f"📥 Commit {n_commit} case(s)",
                            type="primary",
                            disabled=not bulk_confirm,
                            use_container_width=True,
                            key="bulk_commit",
                        ):
                            _db_ins = get_db()
                            inserted, skipped = 0, 0
                            try:
                                cr_obj = _db_ins.get(Courtroom, selected_id)
                                for row_dict, raw_row in zip(
                                    preview_rows, raw_df.itertuples(index=False)
                                ):
                                    if row_dict["Status"] == "❌ Error":
                                        skipped += 1; continue
                                    ct  = str(getattr(raw_row, "case_type")).strip().lower()
                                    ht  = str(getattr(raw_row, "hearing_type")).strip().lower()
                                    cmp = str(getattr(raw_row, "complexity")).strip().lower()
                                    acc = str(getattr(raw_row, "accused_name")).strip()
                                    h_d = pd.to_datetime(
                                        str(getattr(raw_row, "hearing_date"))
                                    ).date()
                                    h_t = datetime.strptime(
                                        str(getattr(raw_row, "start_time")).strip(), "%H:%M"
                                    ).time()
                                    bar = str(getattr(raw_row, "lawyer_bar_number")).strip()
                                    lyr = lawyers_by_bar[bar]
                                    s_dt = datetime.combine(h_d, h_t)
                                    dur  = estimate_duration(ct, ht, cmp)
                                    case_num = next_case_number(_db_ins, ct, selected_id)
                                    nc = Case(
                                        case_number=case_num, case_type=ct,
                                        complexity=cmp, status="active",
                                        defense_lawyer_id=lyr.id,
                                    )
                                    _db_ins.add(nc); _db_ins.flush()
                                    _db_ins.add(Accused(name=acc, case_id=nc.id))
                                    nh = Hearing(
                                        case_id=nc.id, courtroom_id=selected_id,
                                        judge_id=cr_obj.judge.id, hearing_type=ht,
                                        scheduled_start=s_dt,
                                        scheduled_end=s_dt + timedelta(minutes=dur),
                                        estimated_duration_mins=dur, status="scheduled",
                                    )
                                    _db_ins.add(nh); _db_ins.flush()
                                    audit(_db_ins, "HEARING_ADDED", nh.id, {
                                        "case_number": case_num, "case_type": ct,
                                        "hearing_type": ht, "start": s_dt.isoformat(),
                                        "lawyer_id": lyr.id, "source": "bulk_upload",
                                    })
                                    inserted += 1
                                _db_ins.commit()
                                st.success(
                                    f"✅ **{inserted} case(s) added** to "
                                    f"{room_map[selected_id]}. "
                                    + (f"{skipped} row(s) skipped." if skipped else "")
                                )
                                st.rerun()
                            except Exception as e:
                                _db_ins.rollback(); st.error(f"Bulk insert failed: {e}")
                            finally:
                                _db_ins.close()

st.divider()

# ══════════════════════════════════════════════════════════════════════════════
# DOCKET
# ══════════════════════════════════════════════════════════════════════════════

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

    total = len(day_hearings)
    done  = sum(1 for h in day_hearings if h.status == "completed")
    live  = sum(1 for h in day_hearings if h.status == "in_progress")
    sched = sum(1 for h in day_hearings if h.status == "scheduled")
    canc  = sum(1 for h in day_hearings if h.status == "cancelled")

    # ── Item 5: Docket export ──────────────────────────────────────────────────
    _exp_rows = []
    for _h in day_hearings:
        _exp_rows.append({
            "Time":           _h.scheduled_start.strftime("%-I:%M %p"),
            "Case number":    _h.case.case_number,
            "Case type":      _h.case.case_type.title(),
            "Hearing type":   _h.hearing_type.replace("_", " ").title(),
            "Complexity":     _h.case.complexity.title(),
            "Est. mins":      _h.estimated_duration_mins,
            "Accused":        _h.case.accused.name if _h.case.accused else "",
            "Lawyer":         _h.case.defense_lawyer.name if _h.case.defense_lawyer else "",
            "Bar #":          _h.case.defense_lawyer.bar_number if _h.case.defense_lawyer else "",
            "Status":         _h.status.replace("_", " ").title(),
            "Notes":          _h.notes or "",
        })
    _exp_buf = io.BytesIO()
    pd.DataFrame(_exp_rows).to_csv(_exp_buf, index=False)
    _exp_fname = (
        f"docket_{room_map[selected_id].replace(' ', '_')}_"
        f"{run_date.strftime('%Y-%m-%d')}.csv"
    )

    mx1, mx2, mx3, mx4, mx5, mx6 = st.columns([1, 1, 1, 1, 1, 1])
    mx1.metric("Total", total)
    mx2.metric("Remaining", sched)
    mx3.metric("In Progress", live)
    mx4.metric("Completed", done)
    mx5.metric("Cancelled", canc)
    mx6.download_button(
        "📥 Export CSV",
        data=_exp_buf.getvalue(),
        file_name=_exp_fname,
        mime="text/csv",
        use_container_width=True,
        on_click=_record_export,
        args=(selected_id, str(run_date), _exp_fname),
    )
    st.divider()

    # Load all judges once for substitute-judge selectors
    _all_judges = db.execute(select(Judge).order_by(Judge.name)).scalars().all()
    _judge_opts = {j.id: f"{j.name} (Courtroom {j.courtroom_id})" for j in _all_judges}

    for h in day_hearings:
        case: Case     = h.case
        lawyer: Lawyer = case.defense_lawyer
        accused: Accused = case.accused

        badge = {
            "scheduled":   "🔵",
            "in_progress": "🟢",
            "delayed":     "🟠",
            "completed":   "✅",
            "cancelled":   "❌",
        }.get(h.status, "⚪")

        _conf_prefix = "🔒 " if case.is_confidential else ""
        header = (
            f"{badge} {h.scheduled_start.strftime('%-I:%M %p')} · "
            f"{_conf_prefix}{case.case_number} · "
            f"{h.hearing_type.replace('_', ' ').title()} · "
            f"{case.case_type.title()}"
        )

        with st.expander(header, expanded=(h.status == "in_progress")):
            if case.is_confidential:
                st.warning(
                    "🔒 **Sealed / confidential case.** "
                    "This matter must not be discussed in open court or "
                    "disclosed to unauthorised parties."
                )
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
                _all_acc = db.execute(
                    select(Accused).where(Accused.case_id == case.id).order_by(Accused.id)
                ).scalars().all()
                for _i, _acc in enumerate(_all_acc):
                    _label = "Accused" if _i == 0 else "Co-defendant"
                    _ph = f" · {_acc.phone}" if _acc.phone else ""
                    st.write(f"{_label}: {_acc.name}{_ph}")
                if not _all_acc:
                    st.write("Accused: N/A")
                if lawyer and lawyer.bar_number == PRO_SE_BAR:
                    st.write("Representation: Self-Represented (Pro Se)")
                else:
                    st.write(f"Lawyer: {lawyer.name if lawyer else 'N/A'}")
                    if lawyer:
                        st.caption(f"Bar # {lawyer.bar_number}")

            with right:
                st.markdown("**Check-in**")
                if not h.lawyer_checked_in:
                    if st.button("✅ Lawyer arrived", key=f"l_{h.id}",
                                 use_container_width=True):
                        _db = get_db()
                        _db.get(Hearing, h.id).lawyer_checked_in = True
                        _db.commit(); _db.close(); st.rerun()
                else:
                    st.success("Lawyer ✓ checked in")

                if not h.accused_checked_in:
                    if st.button("✅ Accused arrived", key=f"a_{h.id}",
                                 use_container_width=True):
                        _db = get_db()
                        _db.get(Hearing, h.id).accused_checked_in = True
                        _db.commit(); _db.close(); st.rerun()
                else:
                    st.success("Accused ✓ checked in")

            # ── Item 8: Notes ──────────────────────────────────────────────────
            if h.notes:
                st.info(f"📝 **Note:** {h.notes}")
            with st.expander("📝 Edit notes", expanded=False):
                with st.form(f"notes_form_{h.id}"):
                    new_notes = st.text_area(
                        "Notes",
                        value=h.notes or "",
                        height=80,
                        label_visibility="collapsed",
                        placeholder="Interpreter required · Defendant in custody · Remote hearing",
                    )
                    if st.form_submit_button("Save notes", use_container_width=True):
                        _db = get_db()
                        _db.get(Hearing, h.id).notes = new_notes.strip() or None
                        audit(_db, "NOTES_UPDATED", h.id,
                              {"notes": new_notes.strip() or None})
                        _db.commit(); _db.close(); st.rerun()

            st.divider()

            # Status override + Item 6: substitute judge on same row
            sc1, sc2, sc3, sc4 = st.columns([3, 1, 3, 1])
            with sc1:
                new_status = st.selectbox(
                    "Status", ["scheduled", "in_progress", "completed", "delayed", "cancelled"],
                    index=["scheduled", "in_progress", "completed",
                           "delayed", "cancelled"].index(h.status),
                    key=f"s_{h.id}", label_visibility="collapsed",
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
                    _db.commit(); _db.close(); st.rerun()

            # ── Item 6: Substitute judge ───────────────────────────────────────
            with sc3:
                sub_judge_id = st.selectbox(
                    "Judge",
                    options=list(_judge_opts.keys()),
                    index=list(_judge_opts.keys()).index(h.judge_id)
                          if h.judge_id in _judge_opts else 0,
                    format_func=lambda x: _judge_opts[x],
                    key=f"j_{h.id}", label_visibility="collapsed",
                )
            with sc4:
                if st.button("Sub", key=f"jsub_{h.id}", use_container_width=True,
                             help="Assign substitute judge"):
                    if sub_judge_id != h.judge_id:
                        _db = get_db()
                        h2 = _db.get(Hearing, h.id)
                        old_jid = h2.judge_id
                        h2.judge_id = sub_judge_id
                        audit(_db, "JUDGE_SUBSTITUTED", h.id, {
                            "old_judge_id": old_jid,
                            "new_judge_id": sub_judge_id,
                        })
                        _db.commit(); _db.close(); st.rerun()
                    else:
                        st.toast("Same judge selected — no change.")

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
                    other_id = (c.hearing_b_id if c.hearing_a_id == h.id
                                else c.hearing_a_id)
                    db3 = get_db()
                    other      = db3.get(Hearing, other_id)
                    other_room = db3.get(Courtroom, other.courtroom_id) if other else None
                    db3.close()
                    st.warning(
                        f"⚠️ **{lawyer.name}** also scheduled in "
                        f"**{other_room.name if other_room else '?'}** "
                        f"at {other.scheduled_start.strftime('%-I:%M %p') if other else '?'} "
                        f"(overlap {c.overlap_start.strftime('%-I:%M')}–"
                        f"{c.overlap_end.strftime('%-I:%M %p')})"
                    )
                    if st.button("Mark resolved", key=f"res_{c.id}",
                                 use_container_width=True):
                        _db = get_db()
                        _db.get(LawyerConflict, c.id).resolved = True
                        _db.commit(); _db.close(); st.rerun()

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


# ══════════════════════════════════════════════════════════════════════════════
# ALL COURTROOMS — DAILY OVERVIEW  [significant gap #9]
# ══════════════════════════════════════════════════════════════════════════════

st.divider()
with st.expander(
    f"🏛️ All Courtrooms — {run_date.strftime('%A, %B %d %Y')}",
    expanded=False,
):
    _db_ov = get_db()
    try:
        _ov_all = _db_ov.execute(
            select(Hearing)
            .where(Hearing.scheduled_start >= datetime.combine(run_date, time(0, 0)),
                   Hearing.scheduled_start <  datetime.combine(run_date + timedelta(days=1), time(0, 0)))
            .order_by(Hearing.scheduled_start)
        ).scalars().all()

        _cr_list = _db_ov.execute(
            select(Courtroom).order_by(Courtroom.id)
        ).scalars().all()

        ov_rows = []
        for _cr in _cr_list:
            _cr_hs = [_h for _h in _ov_all if _h.courtroom_id == _cr.id]
            _live  = next((_h for _h in _cr_hs if _h.status == "in_progress"), None)
            ov_rows.append({
                "Courtroom":   _cr.name,
                "Judge":       _cr.judge.name if _cr.judge else "—",
                "Total":       len(_cr_hs),
                "Scheduled":   sum(1 for _h in _cr_hs if _h.status == "scheduled"),
                "In Progress": sum(1 for _h in _cr_hs if _h.status == "in_progress"),
                "Completed":   sum(1 for _h in _cr_hs if _h.status == "completed"),
                "Delayed":     sum(1 for _h in _cr_hs if _h.status == "delayed"),
                "Cancelled":   sum(1 for _h in _cr_hs if _h.status == "cancelled"),
                "Current case": (
                    ("🔒 Sealed" if _live.case.is_confidential
                     else f"{_live.case.case_number} · "
                          f"{_live.hearing_type.replace('_', ' ').title()}")
                    if _live else "—"
                ),
            })

        ov_df = pd.DataFrame(ov_rows)
        st.dataframe(
            ov_df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Courtroom":    st.column_config.TextColumn(width="medium"),
                "Judge":        st.column_config.TextColumn(width="medium"),
                "Total":        st.column_config.NumberColumn(width="small"),
                "Scheduled":    st.column_config.NumberColumn(width="small"),
                "In Progress":  st.column_config.NumberColumn(width="small"),
                "Completed":    st.column_config.NumberColumn(width="small"),
                "Delayed":      st.column_config.NumberColumn(width="small"),
                "Cancelled":    st.column_config.NumberColumn(width="small"),
                "Current case": st.column_config.TextColumn(width="large"),
            },
        )

        _total_day = len(_ov_all)
        _done_day  = sum(1 for _h in _ov_all if _h.status == "completed")
        _live_day  = sum(1 for _h in _ov_all if _h.status == "in_progress")
        st.caption(
            f"Courthouse total: **{_total_day}** hearings · "
            f"**{_live_day}** in progress · "
            f"**{_done_day}** completed"
        )
    finally:
        _db_ov.close()
