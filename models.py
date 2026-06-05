"""
models.py — All SQLAlchemy ORM models for the Iron Man Fitness SaaS.

Entity map:
  User ──< CoachClient >── User   (M:M coach ↔ client)
  User ──< UserOnboarding          (1:1 onboarding profile)
  User ──< WorkoutLog >── Exercise (N workout logs per user/exercise)
  User ──< PersonalRecord >── Exercise (1RM records per user/exercise)
  User ──< WorkoutPlan >── Exercise    (current working plan per user/exercise)
  User ──< CoachCalendar           (coach schedule entries)
"""

import enum
from datetime import date, datetime, time

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Enum as SAEnum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from database import Base

# ══════════════════════════════════════════════════════════════════════════════
# Enumerations
# ══════════════════════════════════════════════════════════════════════════════

class UserRole(str, enum.Enum):
    admin  = "admin"   # sees all users and data
    coach  = "coach"   # manages assigned clients
    client = "client"  # athlete (solo or with a coach)


class ExperienceLevel(str, enum.Enum):
    beginner     = "beginner"      # < 1 year, starts Full Body track
    recovery     = "recovery"      # returning after injury/break, same as beginner track
    intermediate = "intermediate"  # 1-3 years
    advanced     = "advanced"      # 3+ years, can handle periodization blocks


class TrainingFocus(str, enum.Enum):
    """Accent the user chose during onboarding."""
    full_body    = "full_body"
    glutes_legs  = "glutes_legs"
    upper_body   = "upper_body"
    core         = "core"
    strength     = "strength"     # pure strength / powerlifting focus


class TrainingPhase(str, enum.Enum):
    """Progression track phases (auto-advanced when phase timer expires)."""
    full_body   = "full_body"    # phase 1 — minimum 3 months for beginners
    upper_lower = "upper_lower"  # phase 2 — upper/lower split
    split       = "split"        # phase 3 — push/pull/legs or body-part split
    strength    = "strength"     # advanced periodization block (3-5 reps)


class MuscleGroup(str, enum.Enum):
    chest       = "chest"
    back        = "back"
    shoulders   = "shoulders"
    biceps      = "biceps"
    triceps     = "triceps"
    quadriceps  = "quadriceps"
    hamstrings  = "hamstrings"
    glutes      = "glutes"
    calves      = "calves"
    core        = "core"
    forearms    = "forearms"


class AppointmentStatus(str, enum.Enum):
    scheduled  = "scheduled"
    completed  = "completed"
    cancelled  = "cancelled"
    no_show    = "no_show"


# ══════════════════════════════════════════════════════════════════════════════
# User
# ══════════════════════════════════════════════════════════════════════════════

class User(Base):
    __tablename__ = "users"

    id              = Column(Integer, primary_key=True, index=True)
    email           = Column(String(255), unique=True, nullable=False, index=True)
    hashed_password = Column(String(255), nullable=False)
    full_name       = Column(String(255))
    role            = Column(SAEnum(UserRole), default=UserRole.client, nullable=False)
    is_active       = Column(Boolean, default=True, nullable=False)
    created_at      = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at      = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # ── Relationships ──────────────────────────────────────────────────────────

    # 1:1 onboarding profile
    onboarding = relationship(
        "UserOnboarding",
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
    )

    # Workout history
    workout_logs = relationship(
        "WorkoutLog",
        back_populates="user",
        cascade="all, delete-orphan",
        order_by="WorkoutLog.date.desc()",
    )

    # As a COACH: all clients linked to this coach
    coached_clients = relationship(
        "CoachClient",
        foreign_keys="CoachClient.coach_id",
        back_populates="coach",
        cascade="all, delete-orphan",
    )

    # As a CLIENT: all coaches linked to this client
    client_coaches = relationship(
        "CoachClient",
        foreign_keys="CoachClient.client_id",
        back_populates="client",
        cascade="all, delete-orphan",
    )

    # Calendar entries as coach
    coach_appointments = relationship(
        "CoachCalendar",
        foreign_keys="CoachCalendar.coach_id",
        back_populates="coach",
        cascade="all, delete-orphan",
    )

    # Calendar entries as client
    client_appointments = relationship(
        "CoachCalendar",
        foreign_keys="CoachCalendar.client_id",
        back_populates="client",
    )

    # Personal records (1RM estimates per exercise)
    personal_records = relationship(
        "PersonalRecord",
        back_populates="user",
        cascade="all, delete-orphan",
    )

    # Active workout plans (current weight + rep targets per exercise)
    workout_plans = relationship(
        "WorkoutPlan",
        back_populates="user",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} email={self.email!r} role={self.role}>"


# ══════════════════════════════════════════════════════════════════════════════
# CoachClient  (M:M bridge table with extra fields)
# ══════════════════════════════════════════════════════════════════════════════

class CoachClient(Base):
    __tablename__ = "coach_clients"
    __table_args__ = (
        # A coach can only be linked to the same client once
        UniqueConstraint("coach_id", "client_id", name="uq_coach_client"),
    )

    id         = Column(Integer, primary_key=True, index=True)
    coach_id   = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    client_id  = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    is_active  = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    notes      = Column(Text)  # coach notes about this client relationship

    coach  = relationship("User", foreign_keys=[coach_id],  back_populates="coached_clients")
    client = relationship("User", foreign_keys=[client_id], back_populates="client_coaches")

    def __repr__(self) -> str:
        return f"<CoachClient coach={self.coach_id} client={self.client_id}>"


# ══════════════════════════════════════════════════════════════════════════════
# UserOnboarding  (1:1 with User)
# ══════════════════════════════════════════════════════════════════════════════

class UserOnboarding(Base):
    """
    Stores the athlete's profile from the onboarding questionnaire.

    Phase logic:
      • Beginners/recovery:  Full Body (≥90 days) → Upper/Lower → Split
      • phase_duration_days  = base (90) + extra_days_added
      • extra_days_added     = missed_workouts × PENALTY_DAYS_PER_MISS  (see services.py)
    """
    __tablename__ = "user_onboardings"

    id                 = Column(Integer, primary_key=True, index=True)
    user_id            = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                                unique=True, nullable=False)

    # ── Questionnaire fields ───────────────────────────────────────────────────
    gender             = Column(String(10))   # 'male' | 'female' | 'other'
    age                = Column(Integer)
    experience         = Column(SAEnum(ExperienceLevel), default=ExperienceLevel.beginner, nullable=False)
    focus              = Column(SAEnum(TrainingFocus),   default=TrainingFocus.full_body,  nullable=False)
    workouts_per_week  = Column(Integer, default=3)   # how many sessions the user plans per week

    # ── Phase tracking ─────────────────────────────────────────────────────────
    current_phase       = Column(SAEnum(TrainingPhase), default=TrainingPhase.full_body, nullable=False)
    phase_start_date    = Column(Date, default=date.today, nullable=False)
    # Base duration (days). Updated by services.extend_phase_duration() when workouts are missed.
    phase_duration_days = Column(Integer, default=90, nullable=False)
    missed_workouts     = Column(Integer, default=0,  nullable=False)
    extra_days_added    = Column(Integer, default=0,  nullable=False)  # cumulative penalty already applied

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User", back_populates="onboarding")

    def __repr__(self) -> str:
        return (
            f"<UserOnboarding user={self.user_id} "
            f"phase={self.current_phase} exp={self.experience}>"
        )


# ══════════════════════════════════════════════════════════════════════════════
# Exercise  (global exercise library)
# ══════════════════════════════════════════════════════════════════════════════

class Exercise(Base):
    """
    Exercise catalogue.

    synergist_muscles   — comma-separated MuscleGroup values, e.g. "triceps,shoulders"
    synergy_coefficient — used by services.calculate_synergy_weight() to propose
                          starting isolation weight relative to a compound 1RM.
                          Example: tricep cable ≈ 0.40 × bench-press 1RM.
    """
    __tablename__ = "exercises"

    id                  = Column(Integer, primary_key=True, index=True)
    name                = Column(String(255), unique=True, nullable=False, index=True)
    target_muscle_group = Column(SAEnum(MuscleGroup), nullable=False)
    synergist_muscles   = Column(String(500))   # comma-separated list
    synergy_coefficient = Column(Float, default=0.40)  # isolation ÷ compound 1RM ratio
    is_compound         = Column(Boolean, default=False, nullable=False)
    description         = Column(Text)
    created_at          = Column(DateTime, default=datetime.utcnow, nullable=False)

    workout_logs     = relationship("WorkoutLog",      back_populates="exercise")
    personal_records = relationship("PersonalRecord",   back_populates="exercise")
    workout_plans    = relationship("WorkoutPlan",       back_populates="exercise")

    def __repr__(self) -> str:
        return f"<Exercise id={self.id} name={self.name!r} muscle={self.target_muscle_group}>"


# ══════════════════════════════════════════════════════════════════════════════
# WorkoutLog
# ══════════════════════════════════════════════════════════════════════════════

class WorkoutLog(Base):
    """
    Single working-set entry for a user on a given date.

    calculated_load — fatigue metric: sets × reps × weight × RPE
                      Used for analytics bars (similar to Garmin Training Load).

    Auto-weight progression logic lives in services.auto_regulate_weight().
    Plateau detection uses the last N logs for the same exercise → services.detect_plateau().
    """
    __tablename__ = "workout_logs"

    id              = Column(Integer, primary_key=True, index=True)
    user_id         = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),  nullable=False, index=True)
    exercise_id     = Column(Integer, ForeignKey("exercises.id", ondelete="RESTRICT"), nullable=False, index=True)
    date            = Column(Date,    nullable=False, default=date.today, index=True)
    weight          = Column(Float,   nullable=False)   # kg
    sets            = Column(Integer, nullable=False)
    reps            = Column(Integer, nullable=False)   # reps completed (per set)
    rpe             = Column(Float)                     # Rate of Perceived Exertion 1-10
    calculated_load = Column(Float)                     # sets × reps × weight × RPE (computed on write)
    notes           = Column(Text)
    created_at      = Column(DateTime, default=datetime.utcnow, nullable=False)

    user     = relationship("User",     back_populates="workout_logs")
    exercise = relationship("Exercise", back_populates="workout_logs")

    def __repr__(self) -> str:
        return (
            f"<WorkoutLog user={self.user_id} ex={self.exercise_id} "
            f"date={self.date} {self.sets}×{self.reps}@{self.weight}kg RPE={self.rpe}>"
        )


# ══════════════════════════════════════════════════════════════════════════════
# CoachCalendar
# ══════════════════════════════════════════════════════════════════════════════

class CoachCalendar(Base):
    """
    Appointment / time-block for a coach.

    client_id is nullable to allow coaches to block time without a client
    (e.g. personal training, admin blocks).

    Supports three calendar view modes:
      • Month  — GROUP BY date, COUNT(id)
      • Week   — filter by ISO week, order by date + time_start
      • Day    — filter by date, order by time_start (hourly grid)
    """
    __tablename__ = "coach_calendar"

    id         = Column(Integer, primary_key=True, index=True)
    coach_id   = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    client_id  = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True,  index=True)
    date       = Column(Date,    nullable=False, index=True)
    time_start = Column(Time,    nullable=False)
    time_end   = Column(Time,    nullable=False)
    title      = Column(String(255))
    notes      = Column(Text)
    status     = Column(SAEnum(AppointmentStatus), default=AppointmentStatus.scheduled, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    coach  = relationship("User", foreign_keys=[coach_id],  back_populates="coach_appointments")
    client = relationship("User", foreign_keys=[client_id], back_populates="client_appointments")

    def __repr__(self) -> str:
        return (
            f"<CoachCalendar coach={self.coach_id} client={self.client_id} "
            f"{self.date} {self.time_start}-{self.time_end}>"
        )


# ══════════════════════════════════════════════════════════════════════════════
# PersonalRecord  (1RM estimates, one per user+exercise)
# ══════════════════════════════════════════════════════════════════════════════

class PersonalRecord(Base):
    """
    Stores the estimated 1-Rep Maximum for each user+exercise pair.

    Created / updated by POST /workouts/first.
    Used by WorkoutPlan initialisation and synergy weight suggestions.
    """
    __tablename__ = "personal_records"
    __table_args__ = (
        UniqueConstraint("user_id", "exercise_id", name="uq_pr_user_exercise"),
    )

    id            = Column(Integer, primary_key=True, index=True)
    user_id       = Column(Integer, ForeignKey("users.id",     ondelete="CASCADE"),   nullable=False, index=True)
    exercise_id   = Column(Integer, ForeignKey("exercises.id", ondelete="RESTRICT"),  nullable=False, index=True)
    one_rm        = Column(Float,   nullable=False)           # calculated via Epley formula
    test_weight   = Column(Float,   nullable=False)           # weight used in the test set
    test_reps     = Column(Integer, nullable=False)           # reps performed to near-failure
    date_recorded = Column(Date,    default=date.today, nullable=False)
    created_at    = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at    = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user     = relationship("User",     back_populates="personal_records")
    exercise = relationship("Exercise", back_populates="personal_records")

    def __repr__(self) -> str:
        return f"<PersonalRecord user={self.user_id} ex={self.exercise_id} 1RM={self.one_rm} kg>"


# ══════════════════════════════════════════════════════════════════════════════
# WorkoutPlan  (active prescription per user+exercise)
# ══════════════════════════════════════════════════════════════════════════════

class WorkoutPlan(Base):
    """
    Tracks the current working weight and rep targets for a user+exercise pair.

    Auto-updated by services.auto_regulate_weight() when the rep ceiling is hit:
      current_weight += weight_increment,  target_reps_min resets.
    """
    __tablename__ = "workout_plans"
    __table_args__ = (
        UniqueConstraint("user_id", "exercise_id", name="uq_plan_user_exercise"),
    )

    id               = Column(Integer, primary_key=True, index=True)
    user_id          = Column(Integer, ForeignKey("users.id",     ondelete="CASCADE"),  nullable=False, index=True)
    exercise_id      = Column(Integer, ForeignKey("exercises.id", ondelete="RESTRICT"), nullable=False, index=True)
    current_weight   = Column(Float,   nullable=False)       # current prescribed working weight (kg)
    target_reps_min  = Column(Integer, default=12)           # rep count after a weight increase
    target_reps_max  = Column(Integer, default=17)           # rep count that triggers progression
    weight_increment = Column(Float,   default=2.5)          # kg added when ceiling is hit
    created_at       = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at       = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user     = relationship("User",     back_populates="workout_plans")
    exercise = relationship("Exercise", back_populates="workout_plans")

    def __repr__(self) -> str:
        return (
            f"<WorkoutPlan user={self.user_id} ex={self.exercise_id} "
            f"{self.current_weight} kg × {self.target_reps_min}-{self.target_reps_max} reps>"
        )
