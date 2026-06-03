"""
schemas.py — Pydantic v2 request / response schemas for all API endpoints.

Sections:
  1. Auth
  2. Onboarding
  3. Exercises / Synergy
  4. First Workout  (1RM + plan initialisation)
  5. Workout Log    (logging + auto-progression + plateau)
  6. Personal Records
"""

from __future__ import annotations

from datetime import date, datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

from models import ExperienceLevel, TrainingFocus, TrainingPhase, UserRole


# ══════════════════════════════════════════════════════════════════════════════
# 1. Auth
# ══════════════════════════════════════════════════════════════════════════════

class UserRegisterRequest(BaseModel):
    """
    Registration payload.

    For **client** role, `experience` and `focus` are **required** — the system
    uses them to generate an individual training programme from day one.
    `gender`, `age` and `workouts_per_week` are optional but recommended.

    For **coach** and **admin** roles, all onboarding fields are ignored.
    """
    email:     EmailStr
    password:  str           = Field(min_length=8, description="Minimum 8 characters")
    full_name: Optional[str] = None
    role:      UserRole      = UserRole.client

    # ── Onboarding fields (required for role='client') ─────────────────────
    gender:            Optional[str]             = Field(
        default=None, pattern="^(male|female|other)$",
        description="Required for clients: 'male' | 'female' | 'other'"
    )
    age:               Optional[int]             = Field(
        default=None, ge=10, le=100,
        description="Athlete age"
    )
    experience:        Optional[ExperienceLevel] = Field(
        default=None,
        description="Required for clients: 'beginner' | 'recovery' | 'intermediate' | 'advanced'"
    )
    focus:             Optional[TrainingFocus]   = Field(
        default=None,
        description="Required for clients: 'full_body' | 'glutes_legs' | 'upper_body' | 'core' | 'strength'"
    )
    workouts_per_week: Optional[int]             = Field(
        default=3, ge=1, le=7,
        description="Sessions per week (default 3)"
    )

    @model_validator(mode="after")
    def _client_requires_onboarding(self) -> "UserRegisterRequest":
        """Blocks client registration if experience or focus are missing."""
        if self.role == UserRole.client:
            if self.experience is None:
                raise ValueError(
                    "Field 'experience' is required for client registration. "
                    "Allowed values: beginner, recovery, intermediate, advanced."
                )
            if self.focus is None:
                raise ValueError(
                    "Field 'focus' is required for client registration. "
                    "Allowed values: full_body, glutes_legs, upper_body, core, strength."
                )
        return self


class TokenResponse(BaseModel):
    access_token: str
    token_type:   str = "bearer"
    user_id:      int
    role:         str


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id:         int
    email:      str
    full_name:  Optional[str]
    role:       UserRole
    is_active:  bool
    created_at: datetime


# ══════════════════════════════════════════════════════════════════════════════
# 2. Onboarding
# ══════════════════════════════════════════════════════════════════════════════

class OnboardingUpdateRequest(BaseModel):
    gender:            Optional[str]             = Field(default=None, pattern="^(male|female|other)$")
    age:               Optional[int]             = Field(default=None, ge=10, le=100)
    experience:        Optional[ExperienceLevel] = None
    focus:             Optional[TrainingFocus]   = None
    workouts_per_week: Optional[int]             = Field(default=None, ge=1, le=7)


class DayScheduleOut(BaseModel):
    day:          str
    session_type: str


class OnboardingStatusResponse(BaseModel):
    user_id:             int
    current_phase:       TrainingPhase
    experience:          ExperienceLevel
    focus:               TrainingFocus
    phase_start_date:    date
    phase_duration_days: int
    days_elapsed:        int
    days_remaining:      int
    phase_end_date:      date
    missed_workouts:     int
    extra_days_added:    int
    next_phase:          Optional[str]
    weekly_schedule:     List[DayScheduleOut]


class MissedWorkoutRequest(BaseModel):
    count: int = Field(default=1, ge=1, le=10, description="Number of missed workouts to record")


# ══════════════════════════════════════════════════════════════════════════════
# 3. Exercises / Synergy
# ══════════════════════════════════════════════════════════════════════════════

class SynergySuggestion(BaseModel):
    exercise_id:          int
    exercise_name:        str
    target_muscle_group:  str
    suggested_weight:     float
    synergy_coefficient:  float


class ExerciseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id:                  int
    name:                str
    target_muscle_group: str
    synergist_muscles:   Optional[str]
    synergy_coefficient: float
    is_compound:         bool
    description:         Optional[str]


# ══════════════════════════════════════════════════════════════════════════════
# 4. First Workout  (1RM + plan initialisation)
# ══════════════════════════════════════════════════════════════════════════════

class FirstWorkoutRequest(BaseModel):
    exercise_id: int
    weight:      float = Field(gt=0, description="Weight used in the test set (kg)")
    reps:        int   = Field(gt=0, le=50, description="Reps completed to near-failure")


class FirstWorkoutResponse(BaseModel):
    exercise_id:          int
    exercise_name:        str
    one_rm:               float
    working_weight:       float
    target_reps_min:      int   = 12
    target_reps_max:      int   = 17
    intensity_percentage: float = 0.65
    synergy_suggestions:  List[SynergySuggestion]
    message:              str


# ══════════════════════════════════════════════════════════════════════════════
# 5. Workout Log  (logging + auto-progression + plateau)
# ══════════════════════════════════════════════════════════════════════════════

class WorkoutLogRequest(BaseModel):
    exercise_id: int
    date:        Optional[date]  = None              # defaults to today if omitted
    weight:      float           = Field(gt=0)
    sets:        int             = Field(gt=0, le=20)
    reps:        int             = Field(gt=0, le=50) # reps completed per set
    rpe:         Optional[float] = Field(default=None, ge=1.0, le=10.0)
    notes:       Optional[str]   = None


class ProgressionOut(BaseModel):
    progressed:      bool
    new_weight:      float
    new_target_reps: int
    message:         str


class PlateauCheckOut(BaseModel):
    action:                     str    # 'deload' | 'periodization' | 'continue'
    message:                    str
    suggested_volume_reduction: float


class WorkoutLogResponse(BaseModel):
    log_id:          int
    exercise_id:     int
    date:            date
    weight:          float
    sets:            int
    reps:            int
    rpe:             Optional[float]
    calculated_load: Optional[float]
    progression:     Optional[ProgressionOut]
    plateau_check:   Optional[PlateauCheckOut]
    message:         str


# ══════════════════════════════════════════════════════════════════════════════
# 6. Personal Records
# ══════════════════════════════════════════════════════════════════════════════

class PersonalRecordResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id:            int
    exercise_id:   int
    one_rm:        float
    test_weight:   float
    test_reps:     int
    date_recorded: date
