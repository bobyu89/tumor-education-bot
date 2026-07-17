"""
環境變數與系統設定（使用 Google Gemini API）
"""
from pydantic_settings import BaseSettings
from pathlib import Path


class Settings(BaseSettings):
    # ── LLM 供應商（換 Sonnet 5 只需改這兩個 + 提供 ANTHROPIC_API_KEY）──
    LLM_PROVIDER: str = "google"              # "google" | "anthropic"
    GOOGLE_API_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""
    PRIMARY_MODEL: str = "gemma-3-12b-it"     # google: gemma-3-12b-it；anthropic: claude-sonnet-5
    SECONDARY_MODEL: str = "gemma-3-12b-it"

    # ── RAG ──
    CHROMA_DB_PATH: str = str(Path(__file__).parent.parent / "chroma_db")
    COLLECTION_NAME: str = "patient_education"
    RAG_TOP_K: int = 5

    # ── 認證 ──
    AUTH_SECRET: str = ""                      # 空則每次啟動隨機（重啟後 token 失效）

    class Config:
        env_file = Path(__file__).parent.parent / ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


settings = Settings()
