# app/routers/auth.py
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Literal, Optional
from google.oauth2 import id_token
from google.auth.transport import requests as grequests
from sqlalchemy.orm import Session
from sqlalchemy import select
from app.core.config import settings
from app.core.auth import create_access_token, get_current_user_id
from app.core.database import get_db
from app.models.User import User

router = APIRouter(prefix="/auth", tags=["auth"])


class GoogleTokenIn(BaseModel):
    token: str


class SetRoleIn(BaseModel):
    role: Literal["teacher", "student"]


def _get_user_or_404(user_id: str, db: Session) -> User:
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


def _issue_token(user: User) -> dict:
    """Centralise token creation so both endpoints stay in sync."""
    access_token = create_access_token({
        "sub": str(user.id),
        "role": user.role,   # "teacher" | "student" | None
        "email": user.email,
    })
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "role": user.role,
    }


@router.post("/google")
async def google_exchange(body: GoogleTokenIn, db: AsyncSession = Depends(get_db)):
    try:
        info = id_token.verify_oauth2_token(
            body.token,
            grequests.Request(),
            settings.GOOGLE_CLIENT_ID,
        )
    except Exception as e:
        print(f"Google token verification failed: {e}")
        raise HTTPException(status_code=401, detail="Invalid Google token")

    google_id = info["sub"]

    result = await db.execute(select(User).where(User.google_id == google_id))
    user = result.scalar_one_or_none()

    if not user:
        user = User(
            google_id=google_id,
            email=info.get("email"),
            name=info.get("name"),
            picture=info.get("picture"),
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)

    return _issue_token(user)
async def _get_user_or_404(user_id: str, db: AsyncSession) -> User:
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.post("/set-role")
async def set_role(
    body: SetRoleIn,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    user = await _get_user_or_404(user_id, db)

    user.role = body.role
    await db.commit()
    await db.refresh(user)

    return _issue_token(user)