"""
環境變數與系統設定（使用 Google Gemini API）
"""
from pydantic_settings import BaseSettings
from pathlib import Path


class Settings(BaseSettings):
    GOOGLE_API_KEY: str
    CHROMA_DB_PATH: str = str(Path(__file__).parent.parent / "chroma_db")
    COLLECTION_NAME: str = "patient_education"
    PRIMARY_MODEL: str = "gemini-2.0-flash"
    SECONDARY_MODEL: str = "gemini-2.0-flash"
    RAG_TOP_K: int = 5

    class Config:
        env_file = Path(__file__).parent.parent / ".env"
        env_file_encoding = "utf-8"


settings = Settings()
