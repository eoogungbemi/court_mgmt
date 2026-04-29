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
    """Safe ALTER TABLE migrations for columns added after initial release."""
    stmts = [
        "ALTER TABLE hearings ADD COLUMN notes TEXT",
    ]
    with engine.connect() as conn:
        for stmt in stmts:
            try:
                conn.execute(text(stmt))
                conn.commit()
            except Exception:
                pass  # column already exists — ignore


def init_db() -> None:
    """Create tables, apply migrations, and auto-seed if the database is empty.

    Streamlit Community Cloud has an ephemeral filesystem — the SQLite
    file is lost on every restart. This ensures the app always has data
    without requiring a manual seed step.
    """
    Base.metadata.create_all(bind=engine)
    _apply_migrations()
    _auto_seed_if_empty()


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
