"""
回答品質閘：避免模型在沒有衛教依據時「編造」內容。

兩道關卡：
  前置 pre_check(rag_docs)：RAG 沒有合格來源 → 根本不呼叫 LLM，直接回固定文字。
  後置 classify(reply, rag_docs)：LLM 回答後，標記品質，落 messages.answer_quality。

品質標籤：
  grounded              有來源且正常作答
  deflected_no_source   無來源，前置就擋下
  deflected_off_source  有來源但模型自己說「需請護理師」（或明顯離題）
  redflag_shortcut      紅旗短路，未經 LLM（由 main.py 標記）

注意：這裡不做嚴格的逐句 grounding 驗證（那需要再一次 LLM 呼叫，成本高、易誤判）。
第一版採務實作法：無來源就擋、偵測模型自述無法回答。更嚴謹的 grounding 稽核列為後期。
"""
from __future__ import annotations

# 固定轉介文字（無來源時使用，確定性、不經 LLM）
DEFLECT_TEXT = "這個問題我需要請護理師為您解答，我先幫您記錄下來，護理師會盡快回覆您。"

# 模型自述無法回答的訊號（表示它自己踩了誠實邊界）
_DEFLECT_SIGNALS = ["請護理師", "無法回答", "沒有相關", "建議您諮詢", "無法提供", "請詢問醫"]


def pre_check(rag_docs: list[dict]) -> bool:
    """是否有可用的衛教來源。rag.py 已濾掉 distance >= 0.8 的結果，故空即無來源。"""
    return len(rag_docs) > 0


def classify(reply: str, rag_docs: list[dict]) -> str:
    """LLM 回答後標記品質。"""
    if not rag_docs:
        return "deflected_no_source"
    if any(sig in reply for sig in _DEFLECT_SIGNALS):
        return "deflected_off_source"
    return "grounded"
