"""
環境變數與系統設定（使用 Google Gemini API）
"""
from pydantic import field_validator
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
    # token 簽章密鑰。正式環境務必設定固定值（多 worker 部署需一致）；
    # 空值時每次啟動隨機產生，重啟後所有人需重新登入。
    AUTH_SECRET: str = ""

    # ── 帳號初始化（預設皆為安全值）──
    DEMO_SEED: bool = False                    # True 才植入 P001/R001/A001 示範帳號（僅限本機）
    ADMIN_ACCOUNT: str = "admin"               # 若尚無管理者且 ADMIN_PASSWORD 有值，啟動時建立
    ADMIN_PASSWORD: str = ""

    # ── 網頁 ──
    # 前端與 API 同網域時不需要 CORS。只有前端放在別的網域才填，以逗號分隔。
    CORS_ORIGINS: str = ""

    # ── LINE（兩者皆有值才啟用 /line/webhook）──
    LINE_CHANNEL_SECRET: str = ""
    LINE_CHANNEL_ACCESS_TOKEN: str = ""

    # 居家病患的緊急聯絡指引（LINE 管道使用；請與醫院確認正確號碼）
    EMERGENCY_CONTACT: str = "請立即撥打 119，或聯絡血液腫瘤科 (02) 8792-7056"

    @field_validator("CHROMA_DB_PATH")
    @classmethod
    def _resolve_chroma_path(cls, v: str) -> str:
        # .env 常寫 CHROMA_DB_PATH=../chroma_db（以 backend/ 為基準）。相對路徑若照執行目錄解析，
        # 從專案根目錄跑腳本時會指到外層資料夾、默默建出一個空向量庫。一律以 backend/ 為基準。
        p = Path(v)
        return str(p if p.is_absolute() else (Path(__file__).parent / p).resolve())

    class Config:
        env_file = Path(__file__).parent.parent / ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


settings = Settings()
