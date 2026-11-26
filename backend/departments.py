"""
科別註冊表：每個科別一個資料夾 knowledge/<科別代號>/manifest.json。

新增科別 = 新增一個 manifest（與它指向的單張資料夾），不需要改程式。說明見 docs/knowledge/新增科別說明.md。

manifest 內容：
  id / name          科別代號與顯示名稱
  source_dir         單張 Markdown 所在資料夾（相對於專案根目錄，或絕對路徑）
  emergency_contact  給病患的緊急聯絡指引；空字串 = 沿用 .env 的 EMERGENCY_CONTACT
  redflag            科別專屬的紅旗設定（發燒等級、體溫門檻、額外規則），見 redflag.RedFlagProfile
  safety_text        覆寫依紅旗類別給病患的第一句話（例如引用本科的單張）
  assessment         症狀評估協定清單（症狀、關鍵字、對應單張、追問題、升級門檻）

本模組只用標準函式庫：scripts/build_pages.py、scripts/index_vault.py 在沒有後端相依套件的環境也要能讀。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KNOWLEDGE_DIR = ROOT / "knowledge"
# 未指定科別時使用。後端啟動時由 config 以 .env 的 DEFAULT_DEPARTMENT 覆寫。
DEFAULT_ID = os.environ.get("DEFAULT_DEPARTMENT", "hemato-oncology")

_REQUIRED = ("id", "name", "source_dir")
_PROTOCOL_REQUIRED = ("key", "name", "keywords")


class ManifestError(ValueError):
    """科別設定檔格式錯誤（啟動時就失敗，不要帶著錯誤設定上線）。"""


@dataclass
class Department:
    id: str
    name: str
    source_dir: Path
    emergency_contact: str = ""
    redflag: dict = field(default_factory=dict)
    safety_text: dict = field(default_factory=dict)
    assessment: list[dict] = field(default_factory=list)


_cache: dict[str, Department] | None = None


def _load_one(path: Path) -> Department:
    try:
        m = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ManifestError(f"{path}：不是合法的 JSON（{e}）") from e
    missing = [k for k in _REQUIRED if not m.get(k)]
    if missing:
        raise ManifestError(f"{path}：缺少必要欄位 {missing}")
    if m["id"] != path.parent.name:
        raise ManifestError(f"{path}：id「{m['id']}」必須與資料夾名稱「{path.parent.name}」相同")
    src = Path(m["source_dir"])
    src = src if src.is_absolute() else (ROOT / src)
    if not src.is_dir():
        raise ManifestError(f"{path}：source_dir 不存在：{src}")
    keys = set()
    for p in m.get("assessment", []):
        miss = [k for k in _PROTOCOL_REQUIRED if not p.get(k)]
        if miss:
            raise ManifestError(f"{path}：assessment 項目缺少 {miss}：{p}")
        if p["key"] in keys:
            raise ManifestError(f"{path}：assessment 的 key 重複：{p['key']}")
        keys.add(p["key"])
        if p.get("field") not in (None, "count", "days"):
            raise ManifestError(f"{path}：assessment.field 只能是 count、days 或省略：{p['key']}")
    return Department(
        id=m["id"], name=m["name"], source_dir=src,
        emergency_contact=m.get("emergency_contact") or "",
        redflag={k: v for k, v in (m.get("redflag") or {}).items() if not k.startswith("_")},
        safety_text={k: v for k, v in (m.get("safety_text") or {}).items() if not k.startswith("_")},
        assessment=m.get("assessment") or [],
    )


def load_all(reload: bool = False) -> dict[str, Department]:
    """讀取 knowledge/*/manifest.json。結果快取；reload=True 重新讀取。"""
    global _cache
    if _cache is None or reload:
        found = {}
        for path in sorted(KNOWLEDGE_DIR.glob("*/manifest.json")):
            d = _load_one(path)
            found[d.id] = d
        if not found:
            raise ManifestError(f"{KNOWLEDGE_DIR} 底下沒有任何科別設定檔（*/manifest.json）")
        _cache = found
    return _cache


def get(dept_id: str | None = None) -> Department:
    """取得科別；未指定時用預設科別。找不到時丟 KeyError。"""
    depts = load_all()
    key = dept_id or DEFAULT_ID
    if key not in depts:
        raise KeyError(f"未知的科別：{key}（可用：{', '.join(depts)}）")
    return depts[key]


def exists(dept_id: str) -> bool:
    return dept_id in load_all()
