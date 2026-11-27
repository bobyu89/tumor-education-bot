"""
知識庫交給模型的方式：RAG（檢索相關段落）或 CAG（整個科別的單張全文，搭配 prompt caching）。

KB_MODE（config）：
  rag   每題檢索 RAG_TOP_K 段（rag.py）
  cag   每題提供本科全部單張（排除「參考資料」「護理指導評值」），固定前段由 Claude 快取
  auto  本科單張總字數 ≤ CAG_MAX_CHARS 用 cag，否則 rag（單張多的科別自動退回 RAG）

CAG 的單張來源與索引相同：repo 的原始單張＋管理後台上架的覆蓋層（停用的不放）。
後台上架、停用、退回時由 leaflets.sync_index() 呼叫 invalidate()，下一題就用新版。
"""
from __future__ import annotations

import threading

import departments
import leaflets
from config import settings
from leaflets import index_vault

_cache: dict[str, list[dict]] = {}
_lock = threading.Lock()


def invalidate() -> None:
    with _lock:
        _cache.clear()


def full_docs(dept_id: str | None = None, code: str | None = None) -> list[dict]:
    """本科全部單張，每個章節一筆（欄位與 rag.query 的結果相同，品質核對與引用標記可共用）。
    code：只取某一份單張（症狀評估後的衛教）。"""
    dept = departments.get(dept_id)
    with _lock:
        if dept.id not in _cache:
            _cache[dept.id] = _load(dept)
        docs = _cache[dept.id]
    return [d for d in docs if code is None or d["code"] == code]


def _load(dept) -> list[dict]:
    docs = []
    for path, fm, body, _ in index_vault.collect_documents(dept.source_dir, leaflets.store_dir() / dept.id):
        if not fm.get("rag_index", False):
            continue
        code = fm.get("code") or path.stem.split()[0]
        topic = fm.get("topic", "")
        for name, content in index_vault.split_sections(body):
            if name in index_vault.EXCLUDED_SECTIONS:
                continue
            text = index_vault.clean_markdown(content)
            if not text:
                continue
            docs.append({"content": text, "source": f"{code} {topic}".strip(), "code": code,
                         "section": name, "topic": topic, "category": fm.get("category", ""),
                         "doc_version": fm.get("doc_version", ""), "publisher": fm.get("source", ""),
                         "score": 1.0})
    docs.sort(key=lambda d: d["code"])   # 依代碼排序且穩定：快取前綴每次都一樣
    return docs


def total_chars(dept_id: str | None = None) -> int:
    return sum(len(d["content"]) for d in full_docs(dept_id))


def mode_for(dept_id: str | None = None) -> str:
    mode = (settings.KB_MODE or "rag").lower()
    if mode == "auto":
        return "cag" if total_chars(dept_id) <= settings.CAG_MAX_CHARS else "rag"
    return mode if mode in ("rag", "cag") else "rag"
