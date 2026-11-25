"""
回答品質閘：避免模型在沒有衛教依據時「編造」內容。

兩道關卡：
  前置 pre_check(rag_docs)：RAG 沒有合格來源 → 根本不呼叫 LLM，直接回固定文字。
  後置 classify(reply, rag_docs)：LLM 回答後，標記品質，落 messages.answer_quality。

品質標籤：
  grounded                有來源，且回答標註的單張代碼都在提供給模型的資料內
  uncited                 有來源，但回答沒有標註任何單張代碼（無法核對依據）
  citation_mismatch       回答標註了「沒有提供給模型」的單張代碼（可能用了資料以外的內容）
  deflected_no_source     無來源，前置就擋下
  deflected_unanswerable  模型依規則輸出「無法回答」標記，由系統改成固定轉介文字
  deflected_off_source    模型自己說「需請護理師」等（未照標記規則，但同樣是轉介）
  redflag_shortcut        紅旗短路，未經 LLM（由 chat_service 標記）

注意：這仍不是逐句的語意 grounding 驗證；引用核對只能確認「標註的出處確實提供給模型」，
不能證明每句話都出自該單張。逐句稽核列為後期（設計論文的專家評分即在評這一點）。
"""
from __future__ import annotations

import re

from prompt import UNANSWERABLE_MARK
from safety_text import deflect_reply

# 固定轉介文字（無來源時使用，確定性、不經 LLM）。依管道不同，見 safety_text.deflect_reply；
# 舊版「護理師會盡快回覆您」是系統做不到的承諾，已移除（2026-11 同儕審查）。
DEFLECT_TEXT = deflect_reply("web", "")

# 模型自述無法回答的訊號（表示它自己踩了誠實邊界）
_DEFLECT_SIGNALS = ["請護理師", "無法回答", "沒有相關", "建議您諮詢", "無法提供", "請詢問醫"]


def pre_check(rag_docs: list[dict]) -> bool:
    """是否有可用的衛教來源。rag.py 已濾掉低於模型相似度門檻的段落，故空即無來源。"""
    return len(rag_docs) > 0


# 括號內「像單張代碼」的字串，例如（ONC-17）、（CV-01）、(DM-102)。用來抓模型編出、知識庫裡不存在的代碼。
_PAREN_CODE = re.compile(r"[（(]\s*([A-Z][A-Z0-9]{1,7}-\d{1,4})\s*[)）]")


def provided_codes(rag_docs: list[dict]) -> set[str]:
    """這次提供給模型的單張代碼。優先用 rag 回傳的 code；舊資料退回來源標籤的第一個字。"""
    out = set()
    for d in rag_docs:
        code = d.get("code") or str(d.get("source", "")).split(" ")[0]
        if code:
            out.add(code)
    return out


def cited_codes(reply: str, known_codes: frozenset[str] | set[str] = frozenset()) -> set[str]:
    """回答中標註的單張代碼：知識庫中已知的代碼（出現在任何位置），加上括號內像代碼的字串。
    不寫死任何科別的代碼格式（v1 只認得「ONC-兩位數字」，換科別就全部變成「沒標出處」）。"""
    cited = {c for c in known_codes if c in reply}
    cited |= set(_PAREN_CODE.findall(reply))
    return cited


def classify(reply: str, rag_docs: list[dict], known_codes: frozenset[str] | set[str] = frozenset()) -> str:
    """LLM 回答後標記品質。known_codes：知識庫所有單張代碼（rag.retriever.known_codes()）。"""
    if not rag_docs:
        return "deflected_no_source"
    if UNANSWERABLE_MARK in reply:
        return "deflected_unanswerable"
    if any(sig in reply for sig in _DEFLECT_SIGNALS):
        return "deflected_off_source"
    cited = cited_codes(reply, known_codes | provided_codes(rag_docs))
    if not cited:
        return "uncited"
    if cited - provided_codes(rag_docs):
        return "citation_mismatch"
    return "grounded"
