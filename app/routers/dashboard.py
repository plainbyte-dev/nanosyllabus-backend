from fastapi import Depends, APIRouter

from app.core.auth import require_role

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

@router.get("/teacher/dashboard")
async def teacher_dashboard(user_id: str = Depends(require_role("teacher"))):
    return {"message": "Welcome, teacher", "user_id": user_id}

@router.get("/student/feed")
async def student_feed(user_id: str = Depends(require_role("student"))):
    return {"message": "Welcome, student", "user_id": user_id}
