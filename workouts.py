"""
routers/workouts.py — Workout logging, first-workout 1RM, auto-progression, plateau detection.

Endpoints:
  POST /workouts/first   — test set → 1RM → WorkoutPlan + synergy suggestions
  POST /workouts/log     — log a working set → auto-progression + plateau check
  GET  /workouts/records — list personal records (1RM estimates)
  GET  /workouts/plan/{exercise_id} — current prescribed weight for an exercise
"""

from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from database import get_db
from models import Exercise, PersonalRecord, User, WorkoutLog, WorkoutPlan
from routers.auth import get_current_user
from schemas import (
    FirstWorkoutRequest,
    FirstWorkoutResponse,
    PersonalRecordResponse,
    PlateauCheckOut,
    ProgressionOut,
    SynergySuggestion,
    WorkoutLogRequest,
    WorkoutLogResponse,
)
from services import (
    auto_regulate_weight,
    calculate_1rm,
    calculate_load,
    calculate_synergy_weight,
    calculate_working_weight,
    detect_plateau,
)

router = APIRouter(prefix="/workouts", tags=["Workouts"])


# ══════════════════════════════════════════════════════════════════════════════
# POST /workouts/first
# ══════════════════════════════════════════════════════════════════════════════

@router.post(
    "/first",
    response_model=FirstWorkoutResponse,
    status_code=status.HTTP_201_CREATED,
    summary="First test set — calculates 1RM and initialises the workout plan",
)
def first_workout(
    payload:      FirstWorkoutRequest,
    db:           Session = Depends(get_db),
    current_user: User    = Depends(get_current_user),
):
    """
    Process the athlete's very first set for an exercise.

    1. Calculates estimated 1RM via the **Epley formula**.
    2. Saves / updates `PersonalRecord`.
    3. Creates / updates `WorkoutPlan` at 65 % of 1RM → 12-17 rep range.
    4. Finds isolation exercises that target the compound's synergist muscles
       and returns suggested starting weights (synergy_coefficient × 1RM).
    """
    exercise: Optional[Exercise] = db.query(Exercise).filter(Exercise.id == payload.exercise_id).first()
    if not exercise:
        raise HTTPException(status_code=404, detail="Exercise not found")

    # ── 1. Epley 1RM ─────────────────────────────────────────────────────────
    one_rm = calculate_1rm(payload.weight, payload.reps)
    if one_rm <= 0:
        raise HTTPException(status_code=400, detail="Invalid weight or reps for 1RM calculation")

    # ── 2. Upsert PersonalRecord ──────────────────────────────────────────────
    pr = (
        db.query(PersonalRecord)
        .filter(
            PersonalRecord.user_id     == current_user.id,
            PersonalRecord.exercise_id == payload.exercise_id,
        )
        .first()
    )
    if pr:
        pr.one_rm      = one_rm
        pr.test_weight = payload.weight
        pr.test_reps   = payload.reps
        pr.date_recorded = date.today()
    else:
        pr = PersonalRecord(
            user_id     = current_user.id,
            exercise_id = payload.exercise_id,
            one_rm      = one_rm,
            test_weight = payload.weight,
            test_reps   = payload.reps,
        )
        db.add(pr)

    # ── 3. Upsert WorkoutPlan ─────────────────────────────────────────────────
    working_weight = calculate_working_weight(one_rm, percentage=0.65)

    plan = (
        db.query(WorkoutPlan)
        .filter(
            WorkoutPlan.user_id     == current_user.id,
            WorkoutPlan.exercise_id == payload.exercise_id,
        )
        .first()
    )
    if plan:
        plan.current_weight  = working_weight
        plan.target_reps_min = 12
        plan.target_reps_max = 17
    else:
        plan = WorkoutPlan(
            user_id        = current_user.id,
            exercise_id    = payload.exercise_id,
            current_weight = working_weight,
        )
        db.add(plan)

    db.commit()

    # ── 4. Build synergy suggestions ──────────────────────────────────────────
    synergy_suggestions: List[SynergySuggestion] = []

    if exercise.synergist_muscles:
        synergist_list = [m.strip() for m in exercise.synergist_muscles.split(",") if m.strip()]

        synergy_exercises = (
            db.query(Exercise)
            .filter(
                Exercise.id != exercise.id,
                Exercise.target_muscle_group.in_(synergist_list),
                Exercise.is_compound == False,  # isolation exercises only
            )
            .all()
        )

        for syn_ex in synergy_exercises:
            suggested = calculate_synergy_weight(one_rm, syn_ex.synergy_coefficient)
            if suggested > 0:
                synergy_suggestions.append(
                    SynergySuggestion(
                        exercise_id         = syn_ex.id,
                        exercise_name       = syn_ex.name,
                        target_muscle_group = syn_ex.target_muscle_group.value,
                        suggested_weight    = suggested,
                        synergy_coefficient = syn_ex.synergy_coefficient,
                    )
                )

    return FirstWorkoutResponse(
        exercise_id          = exercise.id,
        exercise_name        = exercise.name,
        one_rm               = one_rm,
        working_weight       = working_weight,
        target_reps_min      = 12,
        target_reps_max      = 17,
        intensity_percentage = 0.65,
        synergy_suggestions  = synergy_suggestions,
        message=(
            f"1ПМ: {one_rm} кг. "
            f"Робоча вага (65 %): {working_weight} кг × 12-17 повторень. "
            f"Знайдено {len(synergy_suggestions)} синергічних вправ."
        ),
    )


# ══════════════════════════════════════════════════════════════════════════════
# POST /workouts/log
# ══════════════════════════════════════════════════════════════════════════════

@router.post(
    "/log",
    response_model=WorkoutLogResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Log a working set with auto-progression and plateau detection",
)
def log_workout(
    payload:      WorkoutLogRequest,
    db:           Session = Depends(get_db),
    current_user: User    = Depends(get_current_user),
):
    """
    Record a completed working set and run automatic analysis:

    - **calculated_load** = sets × reps × weight × RPE (fatigue bar metric).
    - **Progression**: if `reps >= target_reps_max` → `weight += 2.5 kg`, reps reset to 12.
    - **Plateau check**: analyses last 3 sessions for the same exercise →
      returns `deload` (beginner) or `periodization` (advanced) if stagnation detected.
    """
    if not db.query(Exercise).filter(Exercise.id == payload.exercise_id).first():
        raise HTTPException(status_code=404, detail="Exercise not found")

    log_date = payload.date or date.today()
    calc_load = (
        calculate_load(payload.sets, payload.reps, payload.weight, payload.rpe)
        if payload.rpe else None
    )

    # ── Save log ──────────────────────────────────────────────────────────────
    log = WorkoutLog(
        user_id         = current_user.id,
        exercise_id     = payload.exercise_id,
        date            = log_date,
        weight          = payload.weight,
        sets            = payload.sets,
        reps            = payload.reps,
        rpe             = payload.rpe,
        calculated_load = calc_load,
        notes           = payload.notes,
    )
    db.add(log)
    db.flush()  # get log.id before commit

    # ── Auto-progression ──────────────────────────────────────────────────────
    progression_out: Optional[ProgressionOut] = None

    plan = (
        db.query(WorkoutPlan)
        .filter(
            WorkoutPlan.user_id     == current_user.id,
            WorkoutPlan.exercise_id == payload.exercise_id,
        )
        .first()
    )

    if plan:
        result = auto_regulate_weight(
            current_weight   = plan.current_weight,
            achieved_reps    = payload.reps,
            target_reps_min  = plan.target_reps_min,
            target_reps_max  = plan.target_reps_max,
            weight_increment = plan.weight_increment,
        )
        if result.progressed:
            plan.current_weight  = result.new_weight
            plan.target_reps_min = result.new_target_reps  # → 12, new cycle begins
            plan.target_reps_max = plan.target_reps_max    # → 17, ceiling confirmed (12 → 17 cycle resets)

        progression_out = ProgressionOut(
            progressed      = result.progressed,
            new_weight      = result.new_weight,
            new_target_reps = result.new_target_reps,
            message         = result.message,
        )

    # ── Plateau detection ─────────────────────────────────────────────────────
    plateau_out: Optional[PlateauCheckOut] = None

    if current_user.onboarding:
        # Fetch the last 5 logs for this user+exercise (oldest first) — includes current flush
        recent_raw = (
            db.query(WorkoutLog)
            .filter(
                WorkoutLog.user_id     == current_user.id,
                WorkoutLog.exercise_id == payload.exercise_id,
            )
            .order_by(WorkoutLog.date.desc(), WorkoutLog.id.desc())
            .limit(5)
            .all()
        )
        recent_raw.reverse()  # oldest → newest for detect_plateau()
        recent_logs = [{"reps": r.reps, "rpe": r.rpe or 5.0} for r in recent_raw]

        rec = detect_plateau(recent_logs, current_user.onboarding.experience.value)
        plateau_out = PlateauCheckOut(
            action                     = rec.action,
            message                    = rec.message,
            suggested_volume_reduction = rec.suggested_volume_reduction,
        )

    db.commit()

    return WorkoutLogResponse(
        log_id          = log.id,
        exercise_id     = payload.exercise_id,
        date            = log_date,
        weight          = payload.weight,
        sets            = payload.sets,
        reps            = payload.reps,
        rpe             = payload.rpe,
        calculated_load = calc_load,
        progression     = progression_out,
        plateau_check   = plateau_out,
        message         = "Тренування збережено.",
    )


# ══════════════════════════════════════════════════════════════════════════════
# GET /workouts/records
# ══════════════════════════════════════════════════════════════════════════════

@router.get(
    "/records",
    response_model=List[PersonalRecordResponse],
    summary="List all personal 1RM records for the current user",
)
def get_personal_records(
    db:           Session = Depends(get_db),
    current_user: User    = Depends(get_current_user),
):
    return (
        db.query(PersonalRecord)
        .filter(PersonalRecord.user_id == current_user.id)
        .order_by(PersonalRecord.date_recorded.desc())
        .all()
    )


# ══════════════════════════════════════════════════════════════════════════════
# GET /workouts/plan/{exercise_id}
# ══════════════════════════════════════════════════════════════════════════════

@router.get(
    "/plan/{exercise_id}",
    summary="Get current prescribed weight and rep targets for an exercise",
)
def get_workout_plan(
    exercise_id:  int,
    db:           Session = Depends(get_db),
    current_user: User    = Depends(get_current_user),
):
    """
    Returns the current WorkoutPlan for a given exercise.
    Use `POST /workouts/first` to create a plan if none exists.
    """
    plan = (
        db.query(WorkoutPlan)
        .filter(
            WorkoutPlan.user_id     == current_user.id,
            WorkoutPlan.exercise_id == exercise_id,
        )
        .first()
    )
    if not plan:
        raise HTTPException(
            status_code=404,
            detail="No plan found for this exercise. Run POST /workouts/first first.",
        )
    return {
        "exercise_id":      plan.exercise_id,
        "current_weight":   plan.current_weight,
        "target_reps_min":  plan.target_reps_min,
        "target_reps_max":  plan.target_reps_max,
        "weight_increment": plan.weight_increment,
        "updated_at":       plan.updated_at,
    }
