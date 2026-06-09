from fastapi import Depends, APIRouter

from app.core.auth import require_role

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

@router.get("/teacher/dashboard")
async def teacher_dashboard(user = Depends(require_role("teacher"))):
    return {"message": f"Welcome, {user.name}"}

@router.get("/student/feed")
async def student_feed(user = Depends(require_role("student"))):
    return {"message": f"Welcome, {user.name}"}