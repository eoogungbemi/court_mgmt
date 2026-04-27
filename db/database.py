from sqlalchemy import create_engine, event, select, func
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


def init_db() -> None:
    """Create tables and auto-seed if the database is empty.

    Streamlit Community Cloud has an ephemeral filesystem — the SQLite
    file is lost on every restart. This ensures the app always has data
    without requiring a manual seed step.
    """
    Base.metadata.create_all(bind=engine)
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
