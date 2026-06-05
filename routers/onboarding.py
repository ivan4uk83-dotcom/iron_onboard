"""
routers/onboarding.py — Training phase status, profile update, missed-workout recording.

Endpoints:
  GET  /onboarding/status         — full phase status + weekly schedule
  PUT  /onboarding/update         — update profile (age, focus, experience…)
  POST /onboarding/missed-workout — record skipped sessions → extends phase
"""

from datetime import date, timedelta
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from database import get_db
from models import User, UserOnboarding
from routers.auth import get_current_user
from schemas import (
    DayScheduleOut,
    MissedWorkoutRequest,
    OnboardingStatusResponse,
    OnboardingUpdateRequest,
)
from services import PHASE_BASE_DURATIONS, build_weekly_schedule, extend_phase_duration, get_next_phase

router = APIRouter(prefix="/onboarding", tags=["Onboarding"])

# Default training days by workouts_per_week preference
_DAYS_BY_FREQUENCY: dict[int, List[str]] = {
    1: ["Wednesday"],
    2: ["Tuesday", "Friday"],
    3: ["Monday", "Wednesday", "Friday"],
    4: ["Monday", "Tuesday", "Thursday", "Friday"],
    5: ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"],
    6: ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"],
    7: ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
}


# ── Internal helper — avoids duplicating status-build logic ──────────────────

def _build_status(ob: UserOnboarding, user_id: int) -> OnboardingStatusResponse:
    today         = date.today()
    days_elapsed  = (today - ob.phase_start_date).days
    days_remaining = max(0, ob.phase_duration_days - days_elapsed)
    phase_end_date = ob.phase_start_date + timedelta(days=ob.phase_duration_days)
    next_phase     = get_next_phase(str(ob.current_phase.value), str(ob.experience.value))

    training_days = _DAYS_BY_FREQUENCY.get(ob.workouts_per_week or 3, _DAYS_BY_FREQUENCY[3])
    raw_schedule  = build_weekly_schedule(
        training_days = training_days,
        phase         = str(ob.current_phase.value),
        focus         = str(ob.focus.value),
    )
    schedule = [DayScheduleOut(day=s.day, session_type=s.session_type) for s in raw_schedule]

    return OnboardingStatusResponse(
        user_id             = user_id,
        current_phase       = ob.current_phase,
        experience          = ob.experience,
        focus               = ob.focus,
        phase_start_date    = ob.phase_start_date,
        phase_duration_days = ob.phase_duration_days,
        days_elapsed        = days_elapsed,
        days_remaining      = days_remaining,
        phase_end_date      = phase_end_date,
        missed_workouts     = ob.missed_workouts,
        extra_days_added    = ob.extra_days_added,
        next_phase          = next_phase,
        weekly_schedule     = schedule,
    )


def _get_ob(user: User, db: Session) -> UserOnboarding:
    ob = db.query(UserOnboarding).filter(UserOnboarding.user_id == user.id).first()
    if not ob:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Onboarding profile not found. Register as 'client' to auto-create one.",
        )
    return ob


# ══════════════════════════════════════════════════════════════════════════════
# GET /onboarding/status
# ══════════════════════════════════════════════════════════════════════════════

@router.get(
    "/status",
    response_model=OnboardingStatusResponse,
    summary="Get current training phase and weekly schedule",
)
def get_status(
    db:           Session = Depends(get_db),
    current_user: User    = Depends(get_current_user),
):
    """
    Returns the athlete's full phase overview:
    - **current_phase** — Full Body / Upper-Lower / Split / Strength
    - **days_remaining** — days left in this phase (accounting for missed-workout penalties)
    - **next_phase** — what comes after this phase
    - **weekly_schedule** — day-by-day session types (respects accent/focus)

    Missed workouts extend the phase: each skipped session adds **+2 days**.
    Use `POST /onboarding/missed-workout` to record them.
    """
    ob = _get_ob(current_user, db)
    return _build_status(ob, current_user.id)


# ══════════════════════════════════════════════════════════════════════════════
# PUT /onboarding/update
# ══════════════════════════════════════════════════════════════════════════════

@router.put(
    "/update",
    response_model=OnboardingStatusResponse,
    summary="Update onboarding profile (gender, age, experience, focus, frequency)",
)
def update_onboarding(
    payload:      OnboardingUpdateRequest,
    db:           Session = Depends(get_db),
    current_user: User    = Depends(get_current_user),
):
    """
    Partial update — only provided fields are changed.
    Changing `experience` or `focus` immediately reflects in the weekly schedule.
    """
    ob = _get_ob(current_user, db)

    if payload.gender            is not None: ob.gender            = payload.gender
    if payload.age               is not None: ob.age               = payload.age
    if payload.experience        is not None: ob.experience        = payload.experience
    if payload.focus             is not None: ob.focus             = payload.focus
    if payload.workouts_per_week is not None: ob.workouts_per_week = payload.workouts_per_week

    db.commit()
    db.refresh(ob)
    return _build_status(ob, current_user.id)


# ══════════════════════════════════════════════════════════════════════════════
# POST /onboarding/missed-workout
# ══════════════════════════════════════════════════════════════════════════════

@router.post(
    "/missed-workout",
    response_model=OnboardingStatusResponse,
    summary="Record missed workouts — automatically extends current phase",
)
def record_missed_workout(
    payload:      MissedWorkoutRequest,
    db:           Session = Depends(get_db),
    current_user: User    = Depends(get_current_user),
):
    """
    Records skipped sessions and recalculates the phase end date.

    **Rule**: +2 days per missed workout, added on top of the experience-based base:
    - `beginner` / `recovery`  → base **90 days** (Full Body minimum 3 months)
    - `intermediate`           → base **60 days**
    - `advanced`               → base **45 days**

    Example (beginner, 5 missed): 90 + (5 × 2) = **100 days**.
    """
    ob = _get_ob(current_user, db)

    ob.missed_workouts += payload.count

    base_days = PHASE_BASE_DURATIONS.get(str(ob.experience.value), 90)
    info = extend_phase_duration(
        base_duration_days    = base_days,
        missed_workouts       = ob.missed_workouts,
        penalty_days_per_miss = 2,
    )
    ob.phase_duration_days = info.total_duration_days
    ob.extra_days_added    = info.extra_days

    db.commit()
    db.refresh(ob)
    return _build_status(ob, current_user.id)
