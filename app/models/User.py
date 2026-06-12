# app/models.py
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from uuid import uuid4
from sqlalchemy import Column, String, Enum
from app.core.database import Base
import enum

class RoleEnum(str, enum.Enum):
    teacher = "teacher"
    student = "student"

class User(Base):
    __tablename__ = "users"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    google_id  = Column(String, unique=True, index=True, nullable=False)
    email      = Column(String, unique=True, nullable=False)
    name       = Column(String, nullable=True)
    picture    = Column(String, nullable=True)
    role       = Column(Enum(RoleEnum), nullable=True)  # null until /set-role