"""
services.py — Pure business-logic functions (no DB calls, no HTTP).

All functions are stateless and independently testable.

Sections:
  1. Epley 1RM formula
  2. Initial working weight (65 % of 1RM → hypertrophy range)
  3. Synergy-based isolation starting weight
  4. Auto-weight progression  (rep-ceiling method)
  5. Calculated Load           (fatigue analytics metric)
  6. Plateau / overreaching detection
  7. Phase duration extension  (missed-workout penalty)
  8. Phase progression logic   (Full Body → Upper/Lower → Split → Strength)
  9. Weekly schedule builder   (accent/focus day injection)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# ══════════════════════════════════════════════════════════════════════════════
# 1. Epley 1-Rep Maximum Formula
# ══════════════════════════════════════════════════════════════════════════════

def calculate_1rm(weight: float, reps: int) -> float:
    """
    Epley formula:  1RM = weight × (1 + reps / 30)

    Most accurate in the 2-12 rep range.
    For reps == 1 the tested weight IS the 1RM.
    Returns 0.0 for invalid inputs.

    Examples:
        calculate_1rm(30, 8)  →  38.0  (first bench-press attempt: 30 kg × 8 reps)
        calculate_1rm(100, 1) → 100.0
    """
    if weight <= 0 or reps <= 0:
        return 0.0
    if reps == 1:
        return round(weight, 2)
    return round(weight * (1.0 + reps / 30.0), 2)


# ══════════════════════════════════════════════════════════════════════════════
# 2. Initial Working Weight for Hypertrophy (65 % of 1RM)
# ══════════════════════════════════════════════════════════════════════════════

def calculate_working_weight(
    one_rm: float,
    percentage: float = 0.65,
    increment: float = 2.5,
) -> float:
    """
    Returns the recommended starting working weight rounded to the nearest
    equipment increment (default 2.5 kg plates).

    percentage: intensity zone
      • 0.65  → ~12-15 reps  (hypertrophy, default)
      • 0.75  → ~8-10 reps   (strength-hypertrophy)
      • 0.85  → ~3-5 reps    (strength block)

    Example:
        1RM = 38 kg  → 38 × 0.65 = 24.7 → rounded to 25.0 kg
    """
    if one_rm <= 0:
        return 0.0
    raw = one_rm * percentage
    return round(raw / increment) * increment


# ══════════════════════════════════════════════════════════════════════════════
# 3. Synergy-Based Isolation Starting Weight
# ══════════════════════════════════════════════════════════════════════════════

def calculate_synergy_weight(
    compound_1rm: float,
    synergy_coefficient: float = 0.40,
    increment: float = 2.5,
) -> float:
    """
    Estimates the starting weight for an isolation exercise based on the
    athlete's compound 1RM and a muscle synergy coefficient stored on Exercise.

    Rationale: if the bench-press 1RM is 80 kg, triceps work ~40 % of that
    load → cable pushdown starting weight ≈ 32 kg.

    Built-in coefficient guidelines:
      • Triceps  (from bench press)  : 0.35 – 0.45
      • Biceps   (from row / pull-up): 0.30 – 0.40
      • Shoulders(from bench press)  : 0.20 – 0.30
      • Hamstrings (from squat)      : 0.40 – 0.50

    Example:
        bench 1RM = 80 kg, coeff = 0.40  →  32.0 kg (cable pushdown)
    """
    if compound_1rm <= 0:
        return 0.0
    raw = compound_1rm * synergy_coefficient
    return round(raw / increment) * increment


# ══════════════════════════════════════════════════════════════════════════════
# 4. Auto-Weight Progression  (Rep-Ceiling Method)
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class ProgressionResult:
    new_weight:       float
    new_target_reps:  int
    progressed:       bool
    message:          str


def auto_regulate_weight(
    current_weight: float,
    achieved_reps: int,
    target_reps_min: int = 12,
    target_reps_max: int = 17,
    weight_increment: float = 2.5,
) -> ProgressionResult:
    """
    Double-progression model: keep weight constant while rep count grows from
    target_reps_min → target_reps_max.  When the ceiling is hit, increase
    weight by weight_increment and reset reps back to target_reps_min.

    Rule:
      achieved_reps >= target_reps_max  →  weight += increment, reps reset to min
      otherwise                         →  maintain weight, note current reps

    Args:
        current_weight  : weight used this session (kg)
        achieved_reps   : reps actually completed (per set)
        target_reps_min : rep count after a weight increase  (default 12)
        target_reps_max : rep count that triggers progression (default 17)
        weight_increment: plate step to add                   (default 2.5 kg)

    Example:
        3 × 17 reps @ 50 kg  →  new weight = 52.5 kg, new target = 12 reps
    """
    if achieved_reps >= target_reps_max:
        new_weight = round(current_weight + weight_increment, 2)
        return ProgressionResult(
            new_weight=new_weight,
            new_target_reps=target_reps_min,
            progressed=True,
            message=(
                f"Відмінно! Ти досяг {achieved_reps} повторень — стеля {target_reps_max} "
                f"пробита. Нова вага: {new_weight} кг, повторення скинуті до {target_reps_min}."
            ),
        )

    return ProgressionResult(
        new_weight=current_weight,
        new_target_reps=target_reps_max,
        progressed=False,
        message=(
            f"Продовжуй на {current_weight} кг. "
            f"Ціль: {target_reps_max} повторень (зараз {achieved_reps})."
        ),
    )


# ══════════════════════════════════════════════════════════════════════════════
# 5. Calculated Load  (Fatigue Analytics Metric)
# ══════════════════════════════════════════════════════════════════════════════

def calculate_load(sets: int, reps: int, weight: float, rpe: float) -> float:
    """
    Calculated Load = Volume × RPE
    Volume = sets × reps × weight (kg)

    The result is stored in WorkoutLog.calculated_load and used to draw
    the daily/weekly training-load bar chart (analogous to Garmin Training Load).

    Example:
        3 sets × 12 reps × 60 kg × RPE 7  →  15 120 (arbitrary load units)
    """
    if any(v <= 0 for v in (sets, reps, weight, rpe)):
        return 0.0
    return round(sets * reps * weight * rpe, 2)


# ══════════════════════════════════════════════════════════════════════════════
# 6. Plateau / Overreaching Detection
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class PlateauRecommendation:
    action:                    str    # 'deload' | 'periodization' | 'continue'
    message:                   str
    suggested_volume_reduction: float  # 0.0–1.0 (e.g. 0.4 = reduce 40 %)


def detect_plateau(
    recent_logs: list[dict],
    experience: str,
    rpe_threshold: float = 9.0,
    consecutive_failures: int = 3,
) -> PlateauRecommendation:
    """
    Analyses the last N workout logs for the same exercise to detect plateau
    or overreaching, and returns an appropriate recommendation.

    A session is a "failure" when EITHER:
      • RPE >= rpe_threshold  (athlete reported near-maximal effort), OR
      • reps did not improve compared to the previous session.

    Outcomes by experience level:
      • beginner / recovery  →  deload: reduce volume by 40 % for one week
      • intermediate/advanced →  periodization: shift to strength block (3-5 reps)

    Args:
        recent_logs          : list of dicts ordered oldest→newest,
                               each with keys {'reps': int, 'rpe': float}
        experience           : 'beginner' | 'recovery' | 'intermediate' | 'advanced'
        rpe_threshold        : RPE value considered a failure (default 9.0)
        consecutive_failures : how many bad sessions in a row trigger action (default 3)

    Example:
        logs = [
            {'reps': 12, 'rpe': 9.5},
            {'reps': 11, 'rpe': 9.5},
            {'reps': 10, 'rpe': 9.0},
        ]
        detect_plateau(logs, 'beginner')  →  PlateauRecommendation(action='deload', ...)
    """
    if len(recent_logs) < consecutive_failures:
        return PlateauRecommendation(
            action="continue",
            message="Недостатньо даних для аналізу — продовжуй за планом.",
            suggested_volume_reduction=0.0,
        )

    last_n = recent_logs[-consecutive_failures:]
    failure_count = 0

    for i, log in enumerate(last_n):
        rpe_failed = (log.get("rpe") or 0.0) >= rpe_threshold
        rep_failed = False
        if i > 0:
            rep_failed = (log.get("reps") or 0) <= (last_n[i - 1].get("reps") or 0)
        if rpe_failed or rep_failed:
            failure_count += 1

    if failure_count >= consecutive_failures:
        if experience in ("beginner", "recovery"):
            return PlateauRecommendation(
                action="deload",
                message=(
                    f"{consecutive_failures} тренування поспіль не вдалося виконати план. "
                    "Рекомендую тиждень розвантаження: зниж об'єм на 40 %."
                ),
                suggested_volume_reduction=0.40,
            )
        else:  # intermediate / advanced
            return PlateauRecommendation(
                action="periodization",
                message=(
                    f"{consecutive_failures} тренування поспіль не вдалося виконати план. "
                    "Рекомендую перехід на силовий блок: 3–5 повторень @ 85–90 % 1ПМ."
                ),
                suggested_volume_reduction=0.0,
            )

    return PlateauRecommendation(
        action="continue",
        message="Прогрес стабільний — продовжуй за поточним планом.",
        suggested_volume_reduction=0.0,
    )


# ══════════════════════════════════════════════════════════════════════════════
# 7. Phase Duration Extension  (Missed-Workout Penalty)
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class PhaseInfo:
    base_duration_days:  int
    missed_workouts:     int
    penalty_per_miss:    int
    extra_days:          int
    total_duration_days: int


def extend_phase_duration(
    base_duration_days: int = 90,
    missed_workouts: int = 0,
    penalty_days_per_miss: int = 2,
) -> PhaseInfo:
    """
    Extends training phase duration by penalty_days_per_miss × missed_workouts.

    This ensures the athlete completes the required adaptation stimulus even
    if they skipped sessions.

    Args:
        base_duration_days  : minimum phase length in days (default 90 = 3 months)
        missed_workouts     : total sessions skipped so far in this phase
        penalty_days_per_miss: days added per missed workout (default 2)

    Example:
        5 missed workouts → 90 + (5 × 2) = 100 days total phase duration
    """
    extra = missed_workouts * penalty_days_per_miss
    return PhaseInfo(
        base_duration_days=base_duration_days,
        missed_workouts=missed_workouts,
        penalty_per_miss=penalty_days_per_miss,
        extra_days=extra,
        total_duration_days=base_duration_days + extra,
    )


# ══════════════════════════════════════════════════════════════════════════════
# 8. Phase Progression Logic
# ══════════════════════════════════════════════════════════════════════════════

# Base phase duration (days) by experience level.
# Penalty days from missed workouts are ADDED on top of this base.
# Core rule: beginners must complete ≥ 90 days of Full Body before advancing.
PHASE_BASE_DURATIONS: dict[str, int] = {
    "beginner":     90,   # 3 months — Full Body phase minimum
    "recovery":     90,   # same track as beginner
    "intermediate": 60,   # 2 months per phase
    "advanced":     45,   # ~6 weeks per phase
}

# Ordered phase tracks per experience level
_PHASE_TRACKS: dict[str, list[str]] = {
    "beginner":     ["full_body", "upper_lower", "split"],
    "recovery":     ["full_body", "upper_lower", "split"],
    "intermediate": ["upper_lower", "split", "strength"],
    "advanced":     ["split", "strength"],
}


def get_next_phase(current_phase: str, experience: str) -> Optional[str]:
    """
    Returns the next training phase in the progression track.
    Returns None if the athlete is already on the final phase.

    Example:
        get_next_phase('full_body', 'beginner')    →  'upper_lower'
        get_next_phase('split',     'beginner')    →  None  (final phase)
        get_next_phase('upper_lower','intermediate') →  'split'
    """
    track = _PHASE_TRACKS.get(str(experience)) or _PHASE_TRACKS.get("beginner", [])
    if current_phase in track:
        idx = track.index(current_phase)
        if idx + 1 < len(track):
            return track[idx + 1]
    return None


# ══════════════════════════════════════════════════════════════════════════════
# 9. Weekly Schedule Builder  (Accent / Focus Day Injection)
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class DaySchedule:
    day:          str   # e.g. 'Monday'
    session_type: str   # e.g. 'full_body' | 'glutes_legs' | 'upper_lower'


def build_weekly_schedule(
    training_days: list[str],
    phase: str,
    focus: str = "full_body",
) -> list[DaySchedule]:
    """
    Builds the weekly workout schedule based on phase and user focus.

    Rules:
      • full_body phase:
          – No accent → all days = 'full_body'
          – With accent (e.g. glutes_legs) → day[0] = accent, rest = 'full_body'
      • upper_lower phase:
          – Alternates 'upper' / 'lower' across available days
      • split phase:
          – Distributes 'push' / 'pull' / 'legs' (or with accent on last slot)
      • strength phase:
          – All days = 'strength'

    Args:
        training_days : ordered list of day names, e.g. ['Monday', 'Wednesday', 'Friday']
        phase         : current TrainingPhase value string
        focus         : user's TrainingFocus value string

    Returns list of DaySchedule (day → session_type).

    Example (Full Body + glutes_legs accent, 3 days):
        ['Monday' → 'glutes_legs', 'Wednesday' → 'full_body', 'Friday' → 'full_body']
    """
    if not training_days:
        return []

    schedule: list[DaySchedule] = []

    if phase == "full_body":
        for i, day in enumerate(training_days):
            session = focus if (i == 0 and focus != "full_body") else "full_body"
            schedule.append(DaySchedule(day=day, session_type=session))

    elif phase == "upper_lower":
        types = ["upper", "lower"]
        for i, day in enumerate(training_days):
            session = types[i % 2]
            # Inject accent on the first 'lower' day if focus is leg/glute related
            if session == "lower" and focus in ("glutes_legs",) and i == 1:
                session = focus
            schedule.append(DaySchedule(day=day, session_type=session))

    elif phase == "split":
        # Push / Pull / Legs rotation; accent replaces last slot if applicable
        base_types = ["push", "pull", "legs"]
        for i, day in enumerate(training_days):
            session = base_types[i % len(base_types)]
            if focus not in ("full_body", "strength") and session == "legs" and i == 2:
                session = focus   # accent day replaces standard 'legs' slot
            schedule.append(DaySchedule(day=day, session_type=session))

    elif phase == "strength":
        for day in training_days:
            schedule.append(DaySchedule(day=day, session_type="strength"))

    else:
        # Fallback for unknown phase
        for day in training_days:
            schedule.append(DaySchedule(day=day, session_type=phase))

    return schedule
