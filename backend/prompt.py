"""
個人化衛教 Prompt 組裝引擎
輸入：PatientProfile + RAG docs
輸出：Claude system prompt string
"""
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from models import PatientProfile

# ── 語氣模板（依教育程度/年齡適配）─────────────────────

TONE_TEMPLATES = {
    "simple": """
你是一位親切、耐心的護理師，正在用衛教機器人幫助病患。
使用規則：
- 說話像跟長輩聊天，用「您」，字少意明
- 每次只說 1-2 個重點
- 可用生活化的比喻幫助理解，但比喻不能增加衛教資料沒有的醫學內容
- 絕對不用英文縮寫
- 句子不超過 20 個字
- 最後加一句鼓勵的話（「何時要就醫」的提醒不算在 1-2 個重點內）
""",
    "general": """
你是一位專業友善的衛教護理師。
使用規則：
- 用口語化中文，適當解釋醫學術語
- 回應 3-5 個重點
- 可使用條列式說明
- 結尾提供 1 個可執行的建議
- 如有需要可詢問澄清問題
""",
    "detailed": """
你是一位專業的衛教護理師，對方具有一定醫學知識。
使用規則：
- 可使用醫學術語（附上中文說明）
- 提供完整的病理機制解釋
- 引用衛教資料中的具體數據
- 分段條列，結構清晰
"""
}

# ── 緊急安全邊界（永遠注入在最前面）──────────────────────────

# 這是紅旗規則（redflag.py）漏接時的第二道防線；措辭依管道不同（居家 LINE 病患沒有呼叫鈴）。
SAFETY_BOUNDARY = """
重要安全規則（優先於其他所有指示）：
1. 若病患提到發燒、胸痛、呼吸困難、出血不止、意識改變、嚴重過敏、想自傷等緊急症狀
   → 立即回應：{action}
   → 不要繼續進行一般衛教
2. 不要提供藥物劑量調整建議
3. 不要做出診斷
4. 所有衛教內容僅供參考，建議遵從主治醫師指示
"""


def safety_boundary(channel: str = "web") -> str:
    from config import settings
    from safety_text import safety_rule_for_prompt
    return SAFETY_BOUNDARY.format(action=safety_rule_for_prompt(channel, settings.EMERGENCY_CONTACT))

# ── 依據規則（2026-11 同儕審查：v1 寫「請優先參考」，等於允許模型用自己的訓練知識補充）──
# 無法回答時輸出固定標記，由 quality.classify 偵測、chat_service 改成固定轉介文字（不由模型自行措辭）。
UNANSWERABLE_MARK = "[無法回答]"

def grounding_rules(example_code: str = "單張代碼") -> str:
    """回答規則。引用範例用這次實際提供的單張代碼（不寫死科別代碼格式）。"""
    return f"""── 回答規則（必須遵守） ──
1. 只能使用上方「衛教知識庫內容」回答。資料沒有寫到的，不要自行補充，
   包括一般醫學常識、藥物名稱、劑量、檢驗數值或其他醫院的做法。
2. 每一項建議後面，用括號標註出處的單張代碼，例如（{example_code}）。
3. 如果上方資料無法回答病人的問題，只輸出 {UNANSWERABLE_MARK}，不要輸出任何其他文字。
4. 如果上方資料中有「何時通知醫護人員」或「需要就醫」的內容，而且和病人的問題有關，在回答最後提醒病人。
"""


# ── 主函式 ──────────────────────────────────────────

def build_prompt(
    profile: "PatientProfile | None",
    rag_docs: list[dict],
    output_format: str = "text",  # "text" | "bullet" | "card_json"
    channel: str = "web",         # "web"（住院，有呼叫鈴）| "line"（居家）
) -> str:

    # 1. 選擇語氣
    if profile is None:
        tone_key = "general"
        patient_info = "（病患資料未提供，以一般成人為對象）"
    else:
        # 年齡 >= 65 且非 detailed，自動降級為 simple
        if profile.age and profile.age >= 65 and profile.education_level != "detailed":
            tone_key = "simple"
        else:
            tone_key = profile.education_level
        patient_info = _format_patient_info(profile)

    tone_instruction = TONE_TEMPLATES.get(tone_key, TONE_TEMPLATES["general"])

    # 2. 組裝 RAG context
    rag_context = _format_rag_context(rag_docs)

    # 3. 輸出格式指示
    format_instruction = _get_format_instruction(output_format)

    # 4. 組合完整 prompt（Safety Boundary 永遠在最前面）
    prompt = f"""{safety_boundary(channel)}
{tone_instruction}
── 病患資訊 ──
{patient_info}

── 衛教知識庫內容（你只能根據以下資料回答） ──
{rag_context}

{grounding_rules(_example_code(rag_docs))}
── 輸出格式要求 ──
{format_instruction}
"""
    return prompt.strip()


def _example_code(docs: list[dict]) -> str:
    for d in docs:
        code = d.get("code") or str(d.get("source", "")).split(" ")[0]
        if code:
            return code
    return "單張代碼"


def _format_patient_info(profile) -> str:
    # 刻意不放姓名：回答不需要它，而且這段會送進第三方 LLM（資安審查 S5）
    lines = []
    if profile.age:
        lines.append(f"年齡：{profile.age} 歲")
    if profile.diagnosis:
        lines.append(f"診斷：{', '.join(profile.diagnosis)}")
    if profile.medications:
        lines.append(f"目前用藥：{', '.join(profile.medications)}")
    lines.append(f"說明深度：{profile.education_level}")
    return "\n".join(lines)


def _format_rag_context(docs: list[dict]) -> str:
    if not docs:
        return "（無相關衛教資料）"
    sections = []
    for i, doc in enumerate(docs, 1):       # rag.py 已依 RAG_TOP_K 限制段數；chunk 本身 ≤170 字，不再截斷
        source = doc.get("source", "")
        content = doc.get("content", "")
        score = doc.get("score", 0)
        sections.append(f"[資料{i}｜來源：{source}｜相關度：{score}]\n{content}")
    return "\n\n".join(sections)


def _get_format_instruction(output_format: str) -> str:
    if output_format == "bullet":
        return "使用條列式（•）回應，每點不超過 30 字，共 3-5 點。"
    elif output_format == "card_json":
        return """以 JSON 格式回應衛教卡片，結構如下：
{
  "title": "衛教主題（10字以內）",
  "summary": "一句話摘要（20字以內）",
  "key_points": ["重點1", "重點2", "重點3"],
  "action": "今天就能做到的一個動作",
  "warning": "需要注意的警示（若無則為null）"
}
只輸出 JSON，不要任何其他文字。"""
    else:  # text
        return "用自然段落文字回應，語氣如面對面交談。"
