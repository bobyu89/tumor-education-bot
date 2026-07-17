"""
紅旗偵測（取代舊 alert.py）。

三層，安全底線永遠在關鍵字：
  1. 關鍵字為底（高召回、確定性）—— HIGH / MEDIUM 兩組。
  2. 否定/語境處理 —— 「沒有胸痛」「如果胸痛」不觸發生理症狀；
     但自傷/心理類 HIGH 從寬（寧可誤報，不可漏接）。
  3. 向量補充（只加分不減分）—— 抓「撐不下去、喘不上來、活著好累」這類
     沒命中關鍵字但語意相近的說法。向量若載入失敗，關鍵字底線仍在。

回傳 RedFlagResult。與舊 AlertResult 欄位相容（is_emergency / severity / matched_keywords），
main.py 可平滑替換。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# ── 關鍵字 ────────────────────────────────────────────────────────
# self_harm 與 psych 兩類不套用否定規則（見下）
HIGH_PHYSICAL = [
    "胸痛", "胸悶", "喘不過氣", "呼吸困難", "心跳很快", "心悸很嚴重",
    "手腳無力", "說話不清楚", "突然頭痛", "暈倒", "失去意識",
    "跌倒了", "流很多血", "大量出血", "骨折",
    "全身起疹子", "臉腫起來", "喉嚨腫", "吞不下",
]
HIGH_SELF_HARM = [
    "想死", "不想活", "活不下去", "自殺", "傷害自己", "結束生命",
    "撐不下去", "活著好累", "沒有意義",
]
MEDIUM = [
    "很痛", "痛很久", "發高燒", "高燒", "吐很多次", "一直吐", "一直拉肚子",
    "藥吃錯了", "忘記吃藥", "血糖很高", "血壓很高", "傷口紅腫", "尿尿有血", "血便",
]

# 否定/假設語境詞：出現在關鍵字「前方近距離」則不觸發（僅用於生理症狀）
NEGATION = ["沒有", "沒", "不會", "不再", "未", "無", "如果", "假如", "萬一", "怕", "擔心會", "避免"]
_NEG_WINDOW = 8   # 關鍵字前 N 字內出現否定詞則抑制


@dataclass
class RedFlagResult:
    is_emergency: bool
    severity: str                       # "none" | "medium" | "high"
    matched_keywords: list[str] = field(default_factory=list)
    via: str = "keyword"                # "keyword" | "vector" | "none"

    # 與舊 AlertResult 相容
    @property
    def is_high(self) -> bool:
        return self.severity == "high"


def _negated(text: str, kw: str) -> bool:
    """關鍵字前 _NEG_WINDOW 字內是否有否定/假設詞。"""
    idx = text.find(kw)
    while idx != -1:
        window = text[max(0, idx - _NEG_WINDOW):idx]
        if not any(neg in window for neg in NEGATION):
            return False          # 至少一處出現且未被否定 → 視為真的觸發
        idx = text.find(kw, idx + 1)
    return True                   # 每一處出現都被否定包住


def _keyword_screen(text: str) -> RedFlagResult:
    # 自傷/心理類：一律不套否定（「不想活」本身就含「不」）
    high_self = [kw for kw in HIGH_SELF_HARM if kw in text]
    if high_self:
        return RedFlagResult(True, "high", high_self, "keyword")

    # 生理類 HIGH：套否定規則
    high_phys = [kw for kw in HIGH_PHYSICAL if kw in text and not _negated(text, kw)]
    if high_phys:
        return RedFlagResult(True, "high", high_phys, "keyword")

    med = [kw for kw in MEDIUM if kw in text and not _negated(text, kw)]
    if med:
        return RedFlagResult(True, "medium", med, "keyword")

    return RedFlagResult(False, "none", [], "none")


# ── 向量補充（惰性載入，失敗則優雅降級）──────────────────────────
# 策展的紅旗範例句：命中關鍵字之外，語意相近也升級
_VECTOR_EXEMPLARS = {
    "high": [
        "我覺得我快不行了", "喘不上來很難受", "整個人快昏過去",
        "我真的撐不住了想結束這一切", "活著好痛苦不想再撐了",
    ],
    "medium": [
        "燒得很厲害退不下來", "吐到整個人虛脫", "肚子絞痛拉個不停",
    ],
}
_VECTOR_THRESHOLD = 0.72    # cosine 相似度門檻（保守，避免誤報過多）

_embedder = None
_exemplar_vecs = None


def _load_vectors():
    global _embedder, _exemplar_vecs
    if _embedder is not None:
        return
    try:
        import numpy as np
        from embedding import make_embedding_function
        ef = make_embedding_function()
        vecs = {}
        for level, phrases in _VECTOR_EXEMPLARS.items():
            arr = np.array(ef(phrases), dtype=np.float32)
            arr = arr / (np.linalg.norm(arr, axis=1, keepdims=True) + 1e-9)
            vecs[level] = arr
        _embedder = ef
        _exemplar_vecs = vecs
        globals()["_np"] = np
    except Exception as e:
        print(f"[redflag] 向量補充停用（關鍵字底線仍運作）：{type(e).__name__}: {str(e)[:80]}")
        _embedder = False   # 標記為載入失敗，不再重試


def _vector_screen(text: str) -> RedFlagResult | None:
    _load_vectors()
    if not _embedder:
        return None
    np = globals()["_np"]
    q = np.array(_embedder([text]), dtype=np.float32)[0]
    q = q / (np.linalg.norm(q) + 1e-9)
    for level in ("high", "medium"):
        sims = _exemplar_vecs[level] @ q
        if float(sims.max()) >= _VECTOR_THRESHOLD:
            return RedFlagResult(True, level, [], "vector")
    return None


# ── 主入口 ────────────────────────────────────────────────────────
def screen(text: str, use_vector: bool = True) -> RedFlagResult:
    """關鍵字為底；未命中且啟用向量時，用向量補一層。取較嚴重者。"""
    kw = _keyword_screen(text)
    if kw.severity == "high":
        return kw                    # 已是最高，不需再看向量
    if not use_vector:
        return kw
    vec = _vector_screen(text)
    if vec is None:
        return kw
    # 取較嚴重的結果（high > medium > none）
    order = {"none": 0, "medium": 1, "high": 2}
    return vec if order[vec.severity] > order[kw.severity] else kw


# 與舊 API 相容的別名，讓既有呼叫端不需大改
def detect_emergency(text: str) -> RedFlagResult:
    return screen(text)
