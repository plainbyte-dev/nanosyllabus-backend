from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt

from app.core.config import settings

bearer_scheme = HTTPBearer(auto_error=False)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except JWTError as e:
        print(f"JWT decode failed: {e} | token preview: {token[:40]}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )


def _get_payload(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> dict:
    """Shared dependency: validates the bearer token and returns its payload."""
    if not credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return decode_token(credentials.credentials)


def get_current_user_id(payload: dict = Depends(_get_payload)) -> str:
    user_id: str = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token payload")
    return user_id


def get_current_user_role(payload: dict = Depends(_get_payload)) -> Optional[str]:
    return payload.get("role")  # "teacher" | "student" | None


def require_role(role: str):
    """Dependency factory — gates a route to a specific role."""
    def checker(payload: dict = Depends(_get_payload)) -> str:
        user_id: str = payload.get("sub")
        user_role: str = payload.get("role")
        if not user_id:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token payload")
        if user_role != role:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Requires '{role}' role")
        return user_id
    return checker
def get_current_teacher_id(payload: dict = Depends(_get_payload)) -> str:
    user_id: str = payload.get("sub")
    role: str = payload.get("role")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token payload")
    if role != "teacher":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Requires 'teacher' role")
    return user_id