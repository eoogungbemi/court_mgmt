from sqlalchemy import (
    Column, Integer, String, DateTime, Boolean,
    ForeignKey, Text, Float, UniqueConstraint
)
from sqlalchemy.orm import relationship, DeclarativeBase
from datetime import datetime


class Base(DeclarativeBase):
    pass


class Courtroom(Base):
    __tablename__ = "courtrooms"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    floor = Column(Integer, nullable=False)

    judge = relationship("Judge", back_populates="courtroom", uselist=False)
    hearings = relationship("Hearing", back_populates="courtroom")


class Judge(Base):
    __tablename__ = "judges"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    courtroom_id = Column(Integer, ForeignKey("courtrooms.id"), unique=True, nullable=False)

    courtroom = relationship("Courtroom", back_populates="judge")
    hearings = relationship("Hearing", back_populates="judge")


class Lawyer(Base):
    __tablename__ = "lawyers"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    bar_number = Column(String, unique=True, nullable=False)
    phone = Column(String, nullable=True)
    email = Column(String, nullable=True)

    cases = relationship("Case", back_populates="defense_lawyer")


class Case(Base):
    __tablename__ = "cases"

    id = Column(Integer, primary_key=True)
    case_number = Column(String, unique=True, nullable=False)
    case_type = Column(String, nullable=False)       # criminal | civil | traffic
    complexity = Column(String, nullable=False)      # low | medium | high
    status = Column(String, default="active")        # active | closed
    defense_lawyer_id = Column(Integer, ForeignKey("lawyers.id"), nullable=False)

    defense_lawyer = relationship("Lawyer", back_populates="cases")
    accused = relationship("Accused", back_populates="case", uselist=False)
    hearings = relationship("Hearing", back_populates="case")


class Accused(Base):
    __tablename__ = "accused"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    case_id = Column(Integer, ForeignKey("cases.id"), unique=True, nullable=False)
    phone = Column(String, nullable=True)

    case = relationship("Case", back_populates="accused")


class Hearing(Base):
    __tablename__ = "hearings"

    id = Column(Integer, primary_key=True)
    case_id = Column(Integer, ForeignKey("cases.id"), nullable=False)
    courtroom_id = Column(Integer, ForeignKey("courtrooms.id"), nullable=False)
    judge_id = Column(Integer, ForeignKey("judges.id"), nullable=False)

    hearing_type = Column(String, nullable=False)
    scheduled_start = Column(DateTime, nullable=False)
    scheduled_end = Column(DateTime, nullable=False)
    estimated_duration_mins = Column(Integer, nullable=False)

    actual_start = Column(DateTime, nullable=True)
    actual_end = Column(DateTime, nullable=True)

    # scheduled | in_progress | completed | delayed | cancelled
    status = Column(String, default="scheduled", nullable=False)

    lawyer_checked_in = Column(Boolean, default=False)
    accused_checked_in = Column(Boolean, default=False)
    notes = Column(Text, nullable=True)

    case = relationship("Case", back_populates="hearings")
    courtroom = relationship("Courtroom", back_populates="hearings")
    judge = relationship("Judge", back_populates="hearings")
    eta_estimates = relationship("ETAEstimate", back_populates="hearing")


class ETAEstimate(Base):
    __tablename__ = "eta_estimates"

    id = Column(Integer, primary_key=True)
    hearing_id = Column(Integer, ForeignKey("hearings.id"), nullable=False)
    estimated_start = Column(DateTime, nullable=False)
    p25_mins = Column(Integer, nullable=False)
    p75_mins = Column(Integer, nullable=False)
    rationale = Column(Text, nullable=True)
    generated_at = Column(DateTime, default=datetime.now)
    agent_name = Column(String, nullable=False)

    hearing = relationship("Hearing", back_populates="eta_estimates")


class LawyerConflict(Base):
    """Persisted conflict flags raised by ConflictDetectorAgent."""
    __tablename__ = "lawyer_conflicts"

    id = Column(Integer, primary_key=True)
    lawyer_id = Column(Integer, ForeignKey("lawyers.id"), nullable=False)
    hearing_a_id = Column(Integer, ForeignKey("hearings.id"), nullable=False)
    hearing_b_id = Column(Integer, ForeignKey("hearings.id"), nullable=False)
    overlap_start = Column(DateTime, nullable=False)
    overlap_end = Column(DateTime, nullable=False)
    resolved = Column(Boolean, default=False)
    detected_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        UniqueConstraint("lawyer_id", "hearing_a_id", "hearing_b_id", name="uq_conflict"),
    )


class AuditLog(Base):
    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True)
    event_type = Column(String, nullable=False)
    agent_name = Column(String, nullable=False)
    entity_type = Column(String, nullable=True)
    entity_id = Column(Integer, nullable=True)
    payload = Column(Text, nullable=True)    # JSON string
    created_at = Column(DateTime, default=datetime.now)
