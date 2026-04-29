import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import io
import json
from datetime import date, datetime, time, timedelta
import pandas as pd
import streamlit as st
from sqlalchemy import select, func

from db.database import init_db, SessionLocal
from db.models import (
    Courtroom, Hearing, Case, Accused, Lawyer,
    LawyerConflict, AuditLog,
)
from config import HEARING_DURATIONS, COMPLEXITY_MULTIPLIER

st.set_page_config(
    page_title="Clerk Dashboard · Court",
    page_icon="🗂️",
    layout="wide",
    initial_sidebar_state="expanded",
)
init_db()


def get_db():
    return SessionLocal()


HEARING_TYPES = {
    "criminal": ["arraignment", "bail_hearing", "preliminary_hearing",
                 "motion_hearing", "plea_hearing", "sentencing", "bench_trial"],
    "civil":    ["status_conference", "motion_hearing", "bench_trial"],
    "traffic":  ["arraignment", "plea_hearing", "bench_trial"],
}


# ── Helpers ───────────────────────────────────────────────────────────────────

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
    clashes = [h for h in hearings if h.id != exclude_hearing_id]
    return len(clashes) == 0


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


# ══════════════════════════════════════════════════════════════════════════════
# CASE MANAGEMENT
# ══════════════════════════════════════════════════════════════════════════════

with st.expander("📁 Case Management — Add · Reschedule · Remove · Bulk Upload", expanded=False):

    add_tab, reschedule_tab, remove_tab, bulk_tab = st.tabs([
        "➕  Add Case",
        "🕐  Set / Change Time",
        "🗑️  Remove Case",
        "📤  Bulk Upload",
    ])

    # ── ADD CASE ──────────────────────────────────────────────────────────────
    with add_tab:
        st.markdown("**Add a new case and hearing to the docket**")
        st.caption(
            "Creates a Case, Accused, and Hearing record. "
            "The case number is generated automatically."
        )

        with st.form("add_case_form", clear_on_submit=True):
            a1, a2 = st.columns(2)

            with a1:
                ac_case_type = st.selectbox(
                    "Case type",
                    ["criminal", "civil", "traffic"],
                    format_func=str.title,
                )
                ac_hearing_type = st.selectbox(
                    "Hearing type",
                    HEARING_TYPES[ac_case_type],
                    format_func=lambda s: s.replace("_", " ").title(),
                )
                ac_complexity = st.selectbox(
                    "Complexity",
                    ["low", "medium", "high"],
                    index=1,
                    format_func=str.title,
                )

            with a2:
                ac_accused = st.text_input("Accused full name")
                ac_date    = st.date_input("Hearing date", value=run_date,
                                           key="ac_date")
                ac_time    = st.time_input("Start time",
                                           value=time(9, 0), step=300,
                                           key="ac_time")

            # Lawyer selector — full list from DB
            _db_l = get_db()
            all_lawyers = _db_l.execute(
                select(Lawyer).order_by(Lawyer.name)
            ).scalars().all()
            _db_l.close()

            lawyer_opts = {l.id: f"{l.name}  ({l.bar_number})"
                           for l in all_lawyers}
            ac_lawyer_id = st.selectbox(
                "Defence lawyer",
                options=list(lawyer_opts.keys()),
                format_func=lambda x: lawyer_opts[x],
            )

            submitted = st.form_submit_button(
                "Add to Docket", type="primary", use_container_width=True
            )

        if submitted:
            if not ac_accused.strip():
                st.error("Accused name is required.")
            else:
                _db = get_db()
                try:
                    start_dt = datetime.combine(ac_date, ac_time)
                    dur      = estimate_duration(ac_case_type, ac_hearing_type,
                                                 ac_complexity)
                    end_dt   = start_dt + timedelta(minutes=dur)

                    # Conflict check
                    free = lawyer_is_free(_db, ac_lawyer_id, start_dt, end_dt)
                    if not free:
                        lawyer_name = lawyer_opts[ac_lawyer_id].split("  ")[0]
                        st.warning(
                            f"⚠️ **{lawyer_name}** already has a hearing "
                            f"overlapping {start_dt.strftime('%-I:%M %p')}–"
                            f"{end_dt.strftime('%-I:%M %p')}. "
                            "Case added but a conflict will be flagged."
                        )

                    courtroom = _db.get(Courtroom, selected_id)
                    case_num  = next_case_number(_db, ac_case_type, selected_id)

                    new_case = Case(
                        case_number=case_num,
                        case_type=ac_case_type,
                        complexity=ac_complexity,
                        status="active",
                        defense_lawyer_id=ac_lawyer_id,
                    )
                    _db.add(new_case)
                    _db.flush()

                    _db.add(Accused(
                        name=ac_accused.strip(),
                        case_id=new_case.id,
                    ))

                    new_hearing = Hearing(
                        case_id=new_case.id,
                        courtroom_id=selected_id,
                        judge_id=courtroom.judge.id,
                        hearing_type=ac_hearing_type,
                        scheduled_start=start_dt,
                        scheduled_end=end_dt,
                        estimated_duration_mins=dur,
                        status="scheduled",
                    )
                    _db.add(new_hearing)
                    _db.flush()

                    audit(_db, "HEARING_ADDED", new_hearing.id, {
                        "case_number": case_num,
                        "case_type":   ac_case_type,
                        "hearing_type": ac_hearing_type,
                        "start": start_dt.isoformat(),
                        "lawyer_id": ac_lawyer_id,
                    })
                    _db.commit()
                    st.success(
                        f"✅ Case **{case_num}** added — "
                        f"{ac_hearing_type.replace('_', ' ').title()} at "
                        f"{start_dt.strftime('%-I:%M %p')} "
                        f"(est. {dur} min)"
                    )
                    st.rerun()
                except Exception as e:
                    _db.rollback()
                    st.error(f"Failed to add case: {e}")
                finally:
                    _db.close()

    # ── RESCHEDULE / SET TIME ─────────────────────────────────────────────────
    with reschedule_tab:
        st.markdown("**Change the scheduled start time for a hearing**")
        st.caption(
            "Updates both the start and end times. "
            "Checks for lawyer conflicts at the new time."
        )

        _db_r = get_db()
        reschedule_hearings = _db_r.execute(
            select(Hearing)
            .where(
                Hearing.courtroom_id == selected_id,
                Hearing.status.in_(["scheduled", "delayed"]),
            )
            .order_by(Hearing.scheduled_start)
        ).scalars().all()
        rs_day = [h for h in reschedule_hearings
                  if h.scheduled_start.date() == run_date]
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
                    rs_date = st.date_input("New date", value=run_date,
                                            key="rs_date")
                with rc2:
                    rs_time = st.time_input("New start time",
                                            value=time(9, 0), step=300,
                                            key="rs_time")

                rs_submitted = st.form_submit_button(
                    "Apply New Time", type="primary", use_container_width=True
                )

            if rs_submitted:
                _db = get_db()
                try:
                    h = _db.get(Hearing, rs_hearing_id)
                    old_start = h.scheduled_start
                    new_start = datetime.combine(rs_date, rs_time)
                    new_end   = new_start + timedelta(
                        minutes=h.estimated_duration_mins
                    )

                    lawyer_id = h.case.defense_lawyer_id
                    free = lawyer_is_free(_db, lawyer_id, new_start, new_end,
                                         exclude_hearing_id=h.id)
                    if not free:
                        st.warning(
                            f"⚠️ Lawyer has a conflict at the new time. "
                            "Time updated — conflict will be flagged."
                        )

                    h.scheduled_start = new_start
                    h.scheduled_end   = new_end
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
                    _db.rollback()
                    st.error(f"Reschedule failed: {e}")
                finally:
                    _db.close()

    # ── REMOVE CASE ───────────────────────────────────────────────────────────
    with remove_tab:
        st.markdown("**Cancel a hearing or remove a case from the docket**")
        st.caption(
            "Cancelling a hearing keeps the record and audit trail intact. "
            "Removing a case permanently deletes it and all its hearings."
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
        rm_day = [h for h in removable if h.scheduled_start.date() == run_date]
        _db_rm.close()

        if not rm_day:
            st.info("No active hearings for the selected courtroom and date.")
        else:
            rm_opts = {
                h.id: (
                    f"{h.scheduled_start.strftime('%-I:%M %p')} · "
                    f"{h.case.case_number} · "
                    f"{h.hearing_type.replace('_', ' ').title()}"
                )
                for h in rm_day
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
                horizontal=True,
                key="rm_action",
            )

            st.warning(
                "**Cancel hearing** sets status to Cancelled — record is kept. \n\n"
                "**Remove case** permanently deletes the case, accused, and all "
                "its hearings. This cannot be undone."
            )

            confirm = st.checkbox("I confirm this action", key="rm_confirm")

            if st.button("Apply", type="primary",
                         disabled=not confirm, key="rm_apply",
                         use_container_width=True):
                _db = get_db()
                try:
                    h = _db.get(Hearing, rm_hearing_id)
                    case_num = h.case.case_number

                    if rm_action == "Cancel hearing only":
                        h.status = "cancelled"
                        audit(_db, "HEARING_CANCELLED", h.id, {
                            "case_number": case_num,
                            "reason": "Cancelled by clerk",
                        })
                        _db.commit()
                        st.success(f"✅ Hearing for **{case_num}** cancelled.")

                    else:
                        # Remove all hearings for this case then the case itself
                        case_obj = h.case
                        hearing_ids = [hh.id for hh in case_obj.hearings]

                        # Clear related records
                        from sqlalchemy import text
                        if hearing_ids:
                            ph = ",".join(str(i) for i in hearing_ids)
                            _db.execute(text(
                                f"DELETE FROM lawyer_conflicts "
                                f"WHERE hearing_a_id IN ({ph}) "
                                f"OR hearing_b_id IN ({ph})"
                            ))
                            _db.execute(text(
                                f"DELETE FROM eta_estimates "
                                f"WHERE hearing_id IN ({ph})"
                            ))

                        audit(_db, "CASE_REMOVED", h.id, {
                            "case_number": case_num,
                            "case_type": case_obj.case_type,
                        })
                        _db.flush()

                        for hh in list(case_obj.hearings):
                            _db.delete(hh)
                        if case_obj.accused:
                            _db.delete(case_obj.accused)
                        _db.delete(case_obj)
                        _db.commit()
                        st.success(
                            f"✅ Case **{case_num}** and all its hearings removed."
                        )

                    st.rerun()
                except Exception as e:
                    _db.rollback()
                    st.error(f"Action failed: {e}")
                finally:
                    _db.close()

    # ── BULK UPLOAD ───────────────────────────────────────────────────────────
    with bulk_tab:
        st.markdown("**Upload a CSV or Excel file to add multiple cases at once**")
        st.caption(
            "All rows are assigned to the courtroom selected in the sidebar. "
            "Rows with validation errors are skipped — valid rows are committed together."
        )

        # ── Template download ─────────────────────────────────────────────────
        _TEMPLATE_COLS = [
            "accused_name", "case_type", "hearing_type",
            "complexity", "hearing_date", "start_time", "lawyer_bar_number",
        ]
        _TEMPLATE_ROWS = [
            ["Jane Smith",   "criminal", "arraignment",       "low",    "2026-04-30", "09:00", "BAR-001"],
            ["John Doe",     "civil",    "status_conference", "medium", "2026-04-30", "10:00", "BAR-002"],
            ["Alice Brown",  "traffic",  "plea_hearing",      "low",    "2026-04-30", "11:00", "BAR-003"],
        ]
        _template_buf = io.BytesIO()
        pd.DataFrame(_TEMPLATE_ROWS, columns=_TEMPLATE_COLS).to_csv(
            _template_buf, index=False
        )
        st.download_button(
            "⬇️  Download template CSV",
            data=_template_buf.getvalue(),
            file_name="case_upload_template.csv",
            mime="text/csv",
            use_container_width=False,
        )

        # ── Valid hearing types flat set for quick lookup ──────────────────────
        _ALL_HEARING_TYPES = {
            ht for hts in HEARING_TYPES.values() for ht in hts
        }

        # ── File uploader ──────────────────────────────────────────────────────
        uploaded = st.file_uploader(
            "Choose a CSV or Excel file",
            type=["csv", "xlsx"],
            key="bulk_uploader",
        )

        if uploaded is not None:
            # Parse
            try:
                if uploaded.name.endswith(".xlsx"):
                    raw_df = pd.read_excel(uploaded, dtype=str)
                else:
                    raw_df = pd.read_csv(uploaded, dtype=str)
            except Exception as e:
                st.error(f"Could not parse file: {e}")
                raw_df = None

            if raw_df is not None:
                raw_df.columns = [c.strip().lower() for c in raw_df.columns]
                missing_cols = [c for c in _TEMPLATE_COLS if c not in raw_df.columns]
                if missing_cols:
                    st.error(
                        f"File is missing required column(s): "
                        f"**{', '.join(missing_cols)}**. "
                        "Download the template above to see the expected format."
                    )
                else:
                    raw_df = raw_df[_TEMPLATE_COLS].copy()
                    raw_df = raw_df.dropna(how="all").reset_index(drop=True)

                    # Load lawyers once for lookup
                    _db_bl = get_db()
                    all_lawyers_bulk = _db_bl.execute(
                        select(Lawyer).order_by(Lawyer.name)
                    ).scalars().all()
                    _db_bl.close()
                    lawyers_by_bar = {l.bar_number: l for l in all_lawyers_bulk}

                    # ── Per-row validation ─────────────────────────────────────
                    preview_rows = []
                    _db_val = get_db()
                    try:
                        for idx, row in raw_df.iterrows():
                            errors, warnings = [], []

                            def _get(col):
                                v = row.get(col, "")
                                return "" if pd.isna(v) else str(v).strip()

                            accused_name  = _get("accused_name")
                            case_type     = _get("case_type").lower()
                            hearing_type  = _get("hearing_type").lower()
                            complexity    = _get("complexity").lower()
                            date_str      = _get("hearing_date")
                            time_str      = _get("start_time")
                            bar_num       = _get("lawyer_bar_number")

                            if not accused_name:
                                errors.append("accused_name is required")
                            if case_type not in HEARING_TYPES:
                                errors.append(f"case_type must be criminal/civil/traffic (got '{case_type}')")
                            elif hearing_type not in HEARING_TYPES[case_type]:
                                valid = ", ".join(HEARING_TYPES[case_type])
                                errors.append(f"hearing_type '{hearing_type}' invalid for {case_type} (valid: {valid})")
                            if complexity not in ("low", "medium", "high"):
                                errors.append(f"complexity must be low/medium/high (got '{complexity}')")

                            h_date, h_time = None, None
                            try:
                                h_date = pd.to_datetime(date_str).date()
                            except Exception:
                                errors.append(f"hearing_date invalid (use YYYY-MM-DD, got '{date_str}')")
                            try:
                                h_time = datetime.strptime(time_str, "%H:%M").time()
                            except Exception:
                                errors.append(f"start_time invalid (use HH:MM 24-hour, got '{time_str}')")

                            lawyer = lawyers_by_bar.get(bar_num)
                            if not lawyer:
                                errors.append(f"No lawyer found with bar # '{bar_num}'")

                            if not errors and h_date and h_time and lawyer:
                                start_dt = datetime.combine(h_date, h_time)
                                dur = estimate_duration(case_type, hearing_type, complexity)
                                end_dt = start_dt + timedelta(minutes=dur)
                                if not lawyer_is_free(_db_val, lawyer.id, start_dt, end_dt):
                                    warnings.append(
                                        f"{lawyer.name} has a scheduling conflict "
                                        f"at {start_dt.strftime('%-I:%M %p')}"
                                    )

                            if errors:
                                status = "❌ Error"
                                notes  = "; ".join(errors)
                            elif warnings:
                                status = "⚠️ Conflict"
                                notes  = "; ".join(warnings)
                            else:
                                status = "✅ Ready"
                                notes  = ""

                            preview_rows.append({
                                "Row":          idx + 1,
                                "Status":       status,
                                "Accused":      accused_name,
                                "Case type":    case_type.title(),
                                "Hearing type": hearing_type.replace("_", " ").title(),
                                "Date":         date_str,
                                "Time":         time_str,
                                "Lawyer bar #": bar_num,
                                "Notes":        notes,
                            })
                    finally:
                        _db_val.close()

                    # ── Preview table ──────────────────────────────────────────
                    preview_df = pd.DataFrame(preview_rows)
                    n_ready    = sum(1 for r in preview_rows if r["Status"] == "✅ Ready")
                    n_conflict = sum(1 for r in preview_rows if r["Status"] == "⚠️ Conflict")
                    n_error    = sum(1 for r in preview_rows if r["Status"] == "❌ Error")
                    n_commit   = n_ready + n_conflict

                    pc1, pc2, pc3 = st.columns(3)
                    pc1.metric("Ready to commit", n_ready, delta=None)
                    pc2.metric("Has conflict (will still commit)", n_conflict)
                    pc3.metric("Errors (will skip)", n_error)

                    st.dataframe(
                        preview_df,
                        use_container_width=True,
                        hide_index=True,
                        column_config={
                            "Status": st.column_config.TextColumn(width="small"),
                            "Notes":  st.column_config.TextColumn(width="large"),
                        },
                    )

                    if n_commit == 0:
                        st.error("No valid rows to commit. Fix the errors above and re-upload.")
                    else:
                        st.info(
                            f"**{n_commit} row(s)** will be added to "
                            f"**{room_map[selected_id]}**. "
                            f"{n_error} error row(s) will be skipped."
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
                                courtroom_obj = _db_ins.get(Courtroom, selected_id)
                                for row_dict, raw_row in zip(
                                    preview_rows, raw_df.itertuples(index=False)
                                ):
                                    if row_dict["Status"] == "❌ Error":
                                        skipped += 1
                                        continue

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

                                    start_dt = datetime.combine(h_d, h_t)
                                    dur      = estimate_duration(ct, ht, cmp)
                                    end_dt   = start_dt + timedelta(minutes=dur)
                                    case_num = next_case_number(_db_ins, ct, selected_id)

                                    new_case = Case(
                                        case_number=case_num,
                                        case_type=ct,
                                        complexity=cmp,
                                        status="active",
                                        defense_lawyer_id=lyr.id,
                                    )
                                    _db_ins.add(new_case)
                                    _db_ins.flush()

                                    _db_ins.add(Accused(
                                        name=acc,
                                        case_id=new_case.id,
                                    ))

                                    new_hearing = Hearing(
                                        case_id=new_case.id,
                                        courtroom_id=selected_id,
                                        judge_id=courtroom_obj.judge.id,
                                        hearing_type=ht,
                                        scheduled_start=start_dt,
                                        scheduled_end=end_dt,
                                        estimated_duration_mins=dur,
                                        status="scheduled",
                                    )
                                    _db_ins.add(new_hearing)
                                    _db_ins.flush()

                                    audit(_db_ins, "HEARING_ADDED", new_hearing.id, {
                                        "case_number":  case_num,
                                        "case_type":    ct,
                                        "hearing_type": ht,
                                        "start":        start_dt.isoformat(),
                                        "lawyer_id":    lyr.id,
                                        "source":       "bulk_upload",
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
                                _db_ins.rollback()
                                st.error(f"Bulk insert failed: {e}")
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

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Total",       total)
    m2.metric("Remaining",   sched)
    m3.metric("In Progress", live)
    m4.metric("Completed",   done)
    m5.metric("Cancelled",   canc)
    st.divider()

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

            # Status override
            st.divider()
            status_opts = ["scheduled", "in_progress", "completed",
                           "delayed", "cancelled"]
            sc1, sc2 = st.columns([3, 1])
            with sc1:
                new_status = st.selectbox(
                    "Update status", status_opts,
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
                    _db.commit(); _db.close(); st.rerun()

            # Conflict details
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
