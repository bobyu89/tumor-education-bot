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


# 病患身分一律來自登入 token（網頁）或 LINE 綁定，請求 body 不再自報 patient_id
# —— 先前可以在 body 填任何代號冒用他人（資安審查 S3）。多送的欄位會被忽略。
class ChatRequest(BaseModel):
    message: str


class QuizSubmit(BaseModel):
    answers: dict            # {題號: 作答}，如 {"1": "O", "4": "3"}
    phase: str = "post"      # pre | post


class PatientCreate(BaseModel):
    pseudonym: str                       # P001 格式
    cancer_type: Optional[str] = None
    age: Optional[int] = None
    gender: Optional[str] = None
    education_level: str = "general"     # simple | general | detailed


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
