"""
Generates synthetic US court data for 5 working days.

Lawyer conflict prevention: a shared day-schedule dict tracks every
(start, end) slot per lawyer across all 20 courtrooms. A lawyer is only
assigned to a hearing if no existing slot overlaps with the new one.
"""

import random
from collections import defaultdict
from datetime import datetime, timedelta, date

from faker import Faker
from sqlalchemy.orm import Session

from config import (
    NUM_COURTROOMS, NUM_LAWYERS,
    COURT_START_HOUR, COURT_END_HOUR,
    BUFFER_BETWEEN_HEARINGS_MINS,
    MAX_LAWYER_HEARINGS_PER_DAY,
    HEARING_DURATIONS, COMPLEXITY_MULTIPLIER,
)
from db.models import (
    Courtroom, Judge, Lawyer, Case, Accused, Hearing,
)

fake = Faker("en_US")
random.seed(42)
Faker.seed(42)

CASE_TYPE_WEIGHTS = [("criminal", 0.55), ("civil", 0.30), ("traffic", 0.15)]
COMPLEXITY_WEIGHTS = [("low", 0.40), ("medium", 0.40), ("high", 0.20)]

CASE_PREFIX = {"criminal": "CR", "civil": "CV", "traffic": "TR"}


# ── helpers ──────────────────────────────────────────────────────────────────

def weighted_choice(choices: list[tuple]) -> str:
    items, weights = zip(*choices)
    return random.choices(items, weights=weights, k=1)[0]


def sample_duration(case_type: str, hearing_type: str, complexity: str) -> int:
    lo, hi = HEARING_DURATIONS[case_type][hearing_type]
    base = random.randint(lo, hi)
    return max(5, int(base * COMPLEXITY_MULTIPLIER[complexity]))


def workweek_dates(num_days: int = 5) -> list[date]:
    """Return the next num_days weekdays starting from today."""
    today = date.today()
    days = []
    d = today
    while len(days) < num_days:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


def overlaps(start_a: datetime, end_a: datetime,
             start_b: datetime, end_b: datetime) -> bool:
    return start_a < end_b and start_b < end_a


def find_available_lawyer(
    lawyers: list[Lawyer],
    start: datetime,
    end: datetime,
    day_schedule: dict[int, list[tuple]],
    day_counts: dict[int, int],
) -> Lawyer | None:
    candidates = [
        l for l in lawyers
        if day_counts[l.id] < MAX_LAWYER_HEARINGS_PER_DAY
    ]
    random.shuffle(candidates)
    for lawyer in candidates:
        existing = day_schedule[lawyer.id]
        if not any(overlaps(start, end, s, e) for s, e in existing):
            return lawyer
    return None


# ── creators ─────────────────────────────────────────────────────────────────

def create_courtrooms(db: Session) -> list[Courtroom]:
    courtrooms = []
    for i in range(1, NUM_COURTROOMS + 1):
        floor = ((i - 1) // 5) + 1
        cr = Courtroom(name=f"Courtroom {i}", floor=floor)
        db.add(cr)
        courtrooms.append(cr)
    db.flush()
    return courtrooms


def create_judges(db: Session, courtrooms: list[Courtroom]) -> list[Judge]:
    judges = []
    for courtroom in courtrooms:
        judge = Judge(
            name=f"Hon. {fake.last_name()}",
            courtroom_id=courtroom.id,
        )
        db.add(judge)
        judges.append(judge)
    db.flush()
    return judges


def create_lawyers(db: Session) -> list[Lawyer]:
    lawyers = []
    for i in range(NUM_LAWYERS):
        state_code = random.choice(["NY", "CA", "TX", "FL", "IL"])
        bar = f"{state_code}-{random.randint(100000, 999999)}"
        lawyer = Lawyer(
            name=f"{fake.first_name()} {fake.last_name()}, Esq.",
            bar_number=bar,
            phone=fake.phone_number(),
            email=fake.ascii_company_email(),
        )
        db.add(lawyer)
        lawyers.append(lawyer)
    db.flush()
    return lawyers


def create_hearing(
    db: Session,
    courtroom: Courtroom,
    judge: Judge,
    hearing_date: date,
    start: datetime,
    case_number: str,
    case_type: str,
    hearing_type: str,
    complexity: str,
    lawyer: Lawyer,
    duration_mins: int,
) -> Hearing:
    end = start + timedelta(minutes=duration_mins)

    case = Case(
        case_number=case_number,
        case_type=case_type,
        complexity=complexity,
        status="active",
        defense_lawyer_id=lawyer.id,
    )
    db.add(case)
    db.flush()

    accused = Accused(
        name=fake.name(),
        case_id=case.id,
        phone=fake.phone_number(),
    )
    db.add(accused)

    hearing = Hearing(
        case_id=case.id,
        courtroom_id=courtroom.id,
        judge_id=judge.id,
        hearing_type=hearing_type,
        scheduled_start=start,
        scheduled_end=end,
        estimated_duration_mins=duration_mins,
        status="scheduled",
    )
    db.add(hearing)
    db.flush()
    return hearing


# ── day scheduler ─────────────────────────────────────────────────────────────

def schedule_courtroom_day(
    db: Session,
    courtroom: Courtroom,
    judge: Judge,
    hearing_date: date,
    lawyers: list[Lawyer],
    day_schedule: dict[int, list[tuple]],
    day_counts: dict[int, int],
    case_counters: dict[str, int],
) -> int:
    """Fill one courtroom for one day. Returns number of hearings created."""
    current = datetime(
        hearing_date.year, hearing_date.month, hearing_date.day,
        COURT_START_HOUR, 0,
    )
    end_of_day = datetime(
        hearing_date.year, hearing_date.month, hearing_date.day,
        COURT_END_HOUR, 0,
    )
    created = 0

    while current < end_of_day:
        case_type = weighted_choice(CASE_TYPE_WEIGHTS)
        hearing_type = random.choice(list(HEARING_DURATIONS[case_type].keys()))
        complexity = weighted_choice(COMPLEXITY_WEIGHTS)
        duration = sample_duration(case_type, hearing_type, complexity)

        slot_end = current + timedelta(minutes=duration)
        if slot_end > end_of_day:
            break

        lawyer = find_available_lawyer(
            lawyers, current, slot_end, day_schedule, day_counts
        )
        if lawyer is None:
            # Advance 30 minutes and retry
            current += timedelta(minutes=30)
            continue

        prefix = CASE_PREFIX[case_type]
        case_counters[prefix] += 1
        case_number = (
            f"{hearing_date.year}-{prefix}-{case_counters[prefix]:05d}"
            f"-CR{courtroom.id}"
        )

        create_hearing(
            db=db,
            courtroom=courtroom,
            judge=judge,
            hearing_date=hearing_date,
            start=current,
            case_number=case_number,
            case_type=case_type,
            hearing_type=hearing_type,
            complexity=complexity,
            lawyer=lawyer,
            duration_mins=duration,
        )

        # Register this slot in the shared day schedule
        day_schedule[lawyer.id].append((current, slot_end))
        day_counts[lawyer.id] += 1

        current = slot_end + timedelta(minutes=BUFFER_BETWEEN_HEARINGS_MINS)
        created += 1

    return created


# ── entry point ───────────────────────────────────────────────────────────────

def seed(db: Session) -> None:
    print("Seeding database...")

    courtrooms = create_courtrooms(db)
    judges = create_judges(db, courtrooms)
    lawyers = create_lawyers(db)

    courtroom_judge = list(zip(courtrooms, judges))
    work_days = workweek_dates(5)
    case_counters: dict[str, int] = defaultdict(int)

    total_hearings = 0

    for day in work_days:
        # Shared lawyer schedule for this day across all courtrooms
        day_schedule: dict[int, list[tuple]] = defaultdict(list)
        day_counts: dict[int, int] = defaultdict(int)

        day_total = 0
        for courtroom, judge in courtroom_judge:
            n = schedule_courtroom_day(
                db=db,
                courtroom=courtroom,
                judge=judge,
                hearing_date=day,
                lawyers=lawyers,
                day_schedule=day_schedule,
                day_counts=day_counts,
                case_counters=case_counters,
            )
            day_total += n

        db.commit()
        print(f"  {day.strftime('%A %Y-%m-%d')}: {day_total} hearings scheduled")
        total_hearings += day_total

    print(f"\nDone. Total hearings: {total_hearings}")
    print(f"Courtrooms: {NUM_COURTROOMS} | Judges: {NUM_COURTROOMS} | Lawyers: {NUM_LAWYERS}")

    # Verify no lawyer conflicts were introduced
    _verify_no_conflicts(db)


def _verify_no_conflicts(db: Session) -> None:
    """Post-seed integrity check: assert no lawyer appears in 2 overlapping hearings."""
    from sqlalchemy import text
    result = db.execute(text("""
        SELECT c.defense_lawyer_id, h1.id, h2.id,
               h1.scheduled_start, h1.scheduled_end,
               h2.scheduled_start, h2.scheduled_end
        FROM hearings h1
        JOIN hearings h2 ON h1.id < h2.id
        JOIN cases c ON h1.case_id = c.id
        JOIN cases c2 ON h2.case_id = c2.id AND c.defense_lawyer_id = c2.defense_lawyer_id
        WHERE h1.scheduled_start < h2.scheduled_end
          AND h2.scheduled_start < h1.scheduled_end
    """)).fetchall()

    if result:
        print(f"\nWARNING: {len(result)} lawyer conflicts detected in seed data.")
        for row in result[:5]:
            print(f"  Lawyer {row[0]}: Hearing {row[1]} vs {row[2]}")
    else:
        print("Conflict check passed: no lawyer appears in two simultaneous hearings.")


if __name__ == "__main__":
    from db.database import init_db, SessionLocal

    init_db()
    db = SessionLocal()
    try:
        seed(db)
    finally:
        db.close()
