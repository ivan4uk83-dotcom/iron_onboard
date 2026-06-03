"""
routers/auth.py — Registration, login, JWT helpers and get_current_user dependency.

Exports:
  router            — include in main.py
  get_current_user  — import in other routers for protected endpoints
  require_role      — role-based access guard factory
"""

import os
from datetime import datetime, timedelta
from typing import Optional

from dotenv import load_dotenv
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from database import get_db
from models import User, UserOnboarding, UserRole
from schemas import TokenResponse, UserRegisterRequest, UserResponse
from services import PHASE_BASE_DURATIONS

load_dotenv()

SECRET_KEY: str = os.getenv("SECRET_KEY", "CHANGE-ME-generate-with-secrets.token_hex(32)")
ALGORITHM:  str = os.getenv("ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60"))

pwd_context   = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")

router = APIRouter(prefix="/auth", tags=["Auth"])


# ── Internal helpers ──────────────────────────────────────────────────────────

def _hash_password(password: str) -> str:
    return pwd_context.hash(password)


def _verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def _create_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    payload = data.copy()
    expire  = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    payload.update({"exp": expire})
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


# ── Reusable dependencies (import these in other routers) ─────────────────────

def get_current_user(
    token: str     = Depends(oauth2_scheme),
    db:    Session = Depends(get_db),
) -> User:
    """
    Decodes the JWT Bearer token and returns the active User.
    Raises 401 if the token is invalid or the user doesn't exist.
    """
    exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload     = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id_str = payload.get("sub")
        if user_id_str is None:
            raise exc
        user_id = int(user_id_str)
    except (JWTError, ValueError):
        raise exc

    user = db.query(User).filter(User.id == user_id, User.is_active == True).first()
    if user is None:
        raise exc
    return user


def require_role(*roles: UserRole):
    """
    Returns a dependency that guards an endpoint by role.

    Usage:
        @router.get("/admin-only")
        def admin_view(user: User = Depends(require_role(UserRole.admin))):
            ...
    """
    def _guard(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Required role(s): {[r.value for r in roles]}",
            )
        return current_user
    return _guard


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user",
)
def register(payload: UserRegisterRequest, db: Session = Depends(get_db)):
    """
    Creates a new User account and returns a JWT token.
    - If role == **client**, requires `experience` + `focus` in the payload and
      creates a fully populated `UserOnboarding` profile in the same transaction.
      Phase duration is set from `PHASE_BASE_DURATIONS` (beginner = 90 days).
    """
    if db.query(User).filter(User.email == payload.email).first():
        raise HTTPException(status_code=400, detail="Email already registered")

    user = User(
        email           = payload.email,
        hashed_password = _hash_password(payload.password),
        full_name       = payload.full_name,
        role            = payload.role,
    )
    db.add(user)
    db.flush()  # assigns user.id before commit

    if payload.role == UserRole.client:
        base_days = PHASE_BASE_DURATIONS.get(str(payload.experience.value), 90)
        db.add(UserOnboarding(
            user_id             = user.id,
            gender              = payload.gender,
            age                 = payload.age,
            experience          = payload.experience,
            focus               = payload.focus,
            workouts_per_week   = payload.workouts_per_week or 3,
            phase_duration_days = base_days,
        ))

    db.commit()
    db.refresh(user)

    token = _create_token({"sub": str(user.id), "role": user.role.value})
    return TokenResponse(access_token=token, user_id=user.id, role=user.role.value)


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Login and receive a JWT token",
)
def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db:        Session                   = Depends(get_db),
):
    """
    Standard OAuth2 password flow.
    **username** field = email address.
    In Swagger UI: click **Authorize** and fill email + password.
    """
    user = db.query(User).filter(User.email == form_data.username).first()
    if not user or not _verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.is_active:
        raise HTTPException(status_code=400, detail="Account is deactivated")

    token = _create_token({"sub": str(user.id), "role": user.role.value})
    return TokenResponse(access_token=token, user_id=user.id, role=user.role.value)


@router.get("/me", response_model=UserResponse, summary="Get current user profile")
def get_me(current_user: User = Depends(get_current_user)):
    """Returns the profile of the currently authenticated user."""
    return current_user
