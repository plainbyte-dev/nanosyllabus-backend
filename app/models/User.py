# app/models.py
from uuid import uuid4

from sqlalchemy import Column, String, Enum
from app.core.database import Base
import enum

class RoleEnum(str, enum.Enum):
    teacher = "teacher"
    student = "student"

class User(Base):
    __tablename__ = "users"

    id         = Column(String, primary_key=True, default=lambda: str(uuid4()))
    google_id  = Column(String, unique=True, index=True, nullable=False)
    email      = Column(String, unique=True, nullable=False)
    name       = Column(String, nullable=True)
    picture    = Column(String, nullable=True)
    role       = Column(Enum(RoleEnum), nullable=True)  # null until /set-role