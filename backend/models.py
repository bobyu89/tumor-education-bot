"""
Pydantic 資料模型定義
"""
from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class PatientProfile(BaseModel):
    patient_id: str
    name: Optional[str] = "病患"
    age: Optional[int] = None
    diagnosis: Optional[list[str]] = []
    medications: Optional[list[str]] = []
    education_level: str = "general"   # simple | general | detailed
    language: str = "zh-TW"


class LoginRequest(BaseModel):
    account: str
    password: str


class ChatRequest(BaseModel):
    patient_id: str
    message: str
    patient_profile: Optional[PatientProfile] = None


class QuizSubmit(BaseModel):
    patient_id: str
    answers: dict            # {題號: 作答}，如 {"1": "O", "4": "3"}
    phase: str = "post"      # pre | post


class ChatResponse(BaseModel):
    response: str
    sources: list[str]
    is_emergency: bool
    emergency_keywords: list[str]
    session_id: str
    timestamp: datetime


class AlertEvent(BaseModel):
    patient_id: str
    patient_name: str
    message: str
    keywords: list[str]
    severity: str    # "high" | "medium"
    timestamp: datetime
