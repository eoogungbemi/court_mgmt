from sqlalchemy import create_engine, event, select, func, text
from sqlalchemy.orm import sessionmaker, Session
from config import DATABASE_URL
from db.models import Base


engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
)


@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_conn, _):
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


def _apply_migrations() -> None:
    """Safe schema migrations for columns and constraints added after initial release."""
    with engine.connect() as conn:
        # 1. hearings.notes column
        try:
            conn.execute(text("ALTER TABLE hearings ADD COLUMN notes TEXT"))
            conn.commit()
        except Exception:
            pass

        # 2. cases.is_confidential column
        try:
            conn.execute(text(
                "ALTER TABLE cases ADD COLUMN is_confidential BOOLEAN DEFAULT 0"
            ))
            conn.commit()
        except Exception:
            pass

        # 2. Remove UNIQUE constraint from accused.case_id to allow co-defendants.
        #    Detected by checking for a non-PK unique index on the table.
        indices = conn.execute(text("PRAGMA index_list(accused)")).fetchall()
        if any(row[2] == 1 for row in indices):
            conn.execute(text("DROP TABLE IF EXISTS accused_v2"))
            conn.execute(text("PRAGMA foreign_keys=OFF"))
            conn.execute(text("""
                CREATE TABLE accused_v2 (
                    id INTEGER PRIMARY KEY,
                    name VARCHAR NOT NULL,
                    case_id INTEGER NOT NULL REFERENCES cases(id),
                    phone VARCHAR
                )
            """))
            conn.execute(text(
                "INSERT INTO accused_v2 SELECT id, name, case_id, phone FROM accused"
            ))
            conn.execute(text("DROP TABLE accused"))
            conn.execute(text("ALTER TABLE accused_v2 RENAME TO accused"))
            conn.execute(text("PRAGMA foreign_keys=ON"))
            conn.commit()


def _ensure_pro_se_lawyer() -> None:
    """Guarantee a PRO-SE placeholder lawyer exists for self-represented defendants."""
    from db.models import Lawyer
    db = SessionLocal()
    try:
        if not db.execute(
            select(Lawyer).where(Lawyer.bar_number == "PRO-SE")
        ).scalar_one_or_none():
            db.add(Lawyer(
                name="Self-Represented (Pro Se)",
                bar_number="PRO-SE",
            ))
            db.commit()
    finally:
        db.close()


def init_db() -> None:
    """Create tables, apply migrations, and auto-seed if the database is empty.

    Streamlit Community Cloud has an ephemeral filesystem — the SQLite
    file is lost on every restart. This ensures the app always has data
    without requiring a manual seed step.
    """
    Base.metadata.create_all(bind=engine)
    _apply_migrations()
    _auto_seed_if_empty()
    _ensure_pro_se_lawyer()


def _auto_seed_if_empty() -> None:
    from db.models import Courtroom
    db = SessionLocal()
    try:
        count = db.execute(select(func.count()).select_from(Courtroom)).scalar()
        if count == 0:
            from db.seed import seed
            seed(db)
    finally:
        db.close()


def get_db() -> Session:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
