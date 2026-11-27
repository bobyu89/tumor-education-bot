"""
環境變數與系統設定
"""
from pydantic import field_validator
from pydantic_settings import BaseSettings
from pathlib import Path


class Settings(BaseSettings):
    # ── LLM 供應商 ──
    # 正式收案：LLM_PROVIDER=anthropic + PRIMARY_MODEL=claude-sonnet-5-5 或 claude-opus-5-5
    LLM_PROVIDER: str = "google"              # "google" | "anthropic" | "mock"
    GOOGLE_API_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""
    PRIMARY_MODEL: str = "gemma-3-12b-it"     # google: gemma-3-12b-it；anthropic: claude-sonnet-5-5 / claude-opus-5-5
    SECONDARY_MODEL: str = "gemma-3-12b-it"
    # Anthropic 專用（Sonnet 5.5 / Opus 5.5 不接受 temperature，改用 effort）
    LLM_EFFORT: str = "low"                   # low | medium | high | xhigh | max；衛教問答用 low 即可
    LLM_MAX_TOKENS: int = 4000                # 含模型思考的 token，不要設太小以免回答被截斷
    LLM_TIMEOUT_SEC: float = 60.0
    # 拒答時是否由 API 改用備援模型。研究收案預設 false：全部回覆出自同一個模型（IRB C202605191 委員一第 1 點）；
    # 拒答時改為同模型重試一次，仍拒答回固定轉介文字。非研究用途想減少拒答時可設 true
    ANTHROPIC_REFUSAL_FALLBACK: bool = False
    # 模型在哪裡運算：us = 只在美國境內（價格 1.1 倍）；global = Anthropic 任一地區；空字串 = 不送這個參數。
    # 研究收案用 us，資料不會送到美國以外的國家（IRB 委員一第 1 點）。另須在 Anthropic Console 使用美國資料區域的工作區
    ANTHROPIC_INFERENCE_GEO: str = "us"

    # ── RAG ──
    CHROMA_DB_PATH: str = str(Path(__file__).parent.parent / "chroma_db")
    COLLECTION_NAME: str = "patient_education"
    RAG_TOP_K: int = 5
    # 知識庫交給模型的方式：rag = 檢索相關段落；cag = 整個科別的單張全文（搭配 prompt caching）；
    # auto = 科別單張總字數不超過 CAG_MAX_CHARS 用 cag，否則 rag。比較結果見 docs/knowledge/RAG與CAG比較.md
    KB_MODE: str = "auto"
    CAG_MAX_CHARS: int = 60000
    # CAG 固定前段的快取保存時間：5m | 1h。病人提問常相隔 5–60 分鐘，1h（寫入 2 倍價）比 5m（每次過期重寫）划算
    CACHE_TTL: str = "1h"

    # ── 認證 ──
    # token 簽章密鑰。正式環境務必設定固定值（多 worker 部署需一致）；
    # 空值時每次啟動隨機產生，重啟後所有人需重新登入。
    AUTH_SECRET: str = ""

    # ── 帳號初始化（預設皆為安全值）──
    DEMO_SEED: bool = False                    # True 才植入 P001/R001/A001 示範帳號（僅限本機）
    ADMIN_ACCOUNT: str = "admin"               # 若尚無管理者且 ADMIN_PASSWORD 有值，啟動時建立
    ADMIN_PASSWORD: str = ""
    # 登入失敗鎖定：同一帳號 LOGIN_LOCK_MINUTES 分鐘內失敗 LOGIN_MAX_FAILURES 次 → 暫停登入
    LOGIN_MAX_FAILURES: int = 5
    LOGIN_LOCK_MINUTES: int = 15

    # ── 網頁 ──
    # 前端與 API 同網域時不需要 CORS。只有前端放在別的網域才填，以逗號分隔。
    CORS_ORIGINS: str = ""

    # ── LINE（兩者皆有值才啟用 /line/webhook）──
    LINE_CHANNEL_SECRET: str = ""
    LINE_CHANNEL_ACCESS_TOKEN: str = ""

    # 居家病患的緊急聯絡指引（LINE 管道使用；請與醫院確認正確號碼）
    EMERGENCY_CONTACT: str = "請立即撥打 119，或聯絡血液腫瘤科 (02) 8792-7056"

    # ── 科別 ── knowledge/<科別>/manifest.json；病患未指定科別時使用
    DEFAULT_DEPARTMENT: str = "hemato-oncology"

    # ── 警示確認時限（分鐘）：超過即在護理端標示「逾時」。⚠️ 預設值待臨床團隊依值班 SOP 確認 ──
    # 見 docs/ops/警示處理SOP_草案.md。系統只負責標示，不會自動打電話或升級。
    ALERT_SLA_HIGH_MIN: int = 15
    ALERT_SLA_MEDIUM_MIN: int = 240
    ALERT_SLA_INFO_MIN: int = 1440

    @field_validator("ANTHROPIC_INFERENCE_GEO")
    @classmethod
    def _check_geo(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in ("us", "global", ""):
            raise ValueError("ANTHROPIC_INFERENCE_GEO 只能是 us、global 或空白")
        return v

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


# 讓只用標準函式庫的模組（departments、redflag）也使用 .env 設定的預設科別
import departments as _departments  # noqa: E402
_departments.DEFAULT_ID = settings.DEFAULT_DEPARTMENT
