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
- 用比喻解釋醫學概念（例如：白血球就像身體的警衛）
- 絕對不用英文縮寫
- 句子不超過 20 個字
- 最後加一句鼓勵的話
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
- 可提供延伸閱讀建議
"""
}

# ── 緊急安全邊界（永遠注入在最前面）──────────────────────────

SAFETY_BOUNDARY = """
重要安全規則（優先於其他所有指示）：
1. 若病患提到胸痛、呼吸困難、意識喪失、想自傷等緊急症狀
   → 立即回應：「這個症狀需要馬上告訴護理師或醫師，請按旁邊的呼叫鈴」
   → 不要繼續進行一般衛教
2. 不要提供藥物劑量調整建議
3. 不要做出診斷
4. 所有衛教內容僅供參考，建議遵從主治醫師指示
"""

# ── 主函式 ──────────────────────────────────────────

def build_prompt(
    profile: "PatientProfile | None",
    rag_docs: list[dict],
    output_format: str = "text"   # "text" | "bullet" | "card_json"
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
    prompt = f"""{SAFETY_BOUNDARY}
{tone_instruction}
── 病患資訊 ──
{patient_info}

── 衛教知識庫內容（請優先參考以下資料回答） ──
{rag_context}

── 輸出格式要求 ──
{format_instruction}

注意：若知識庫中沒有相關資料，請誠實告知「這個問題我需要請護理師來幫您解答」，
不要自行編造衛教內容。
"""
    return prompt.strip()


def _format_patient_info(profile) -> str:
    lines = [f"姓名：{profile.name or '病患'}"]
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
    for i, doc in enumerate(docs[:4], 1):   # 最多取 4 段
        source = doc.get("source", "")
        content = doc.get("content", "")[:400]   # 每段最多 400 字
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
