"""
給病患的固定安全回覆文字（確定性、不經 LLM）。

集中在這裡的原因：
  1. 後端（chat_service / quality / assessment / prompt）與 GitHub Pages 演示引擎共用同一份措辭
     （scripts/build_pages.py 會把 SAFETY_TEXT 匯出到 docs/demo_data.js）。
  2. 2026-11 同儕審查指出 v1 措辭有「不實承諾」：
       - LINE 回覆「系統已記錄並通知醫療團隊」，實際上沒有人在線時根本沒送出去；
       - 轉介文字「護理師會盡快回覆您」，系統裡卻沒有任何待回覆機制；
       - 對居家病患說「請按旁邊的呼叫鈴」。
     原則：**系統做不到的事，不對病患承諾。** LINE 一律聲明「不是 24 小時即時監看」。

本模組不 import 任何第三方套件（build_pages.py 需要在無後端相依套件的環境執行）。
聯絡方式由呼叫端傳入（settings.EMERGENCY_CONTACT），避免 import config。
"""
from __future__ import annotations

# 依紅旗類別的第一句（內容依據衛教單張）
CATEGORY_LEAD = {
    "fever": "化療期間發燒，可能是嚴重感染的警訊，請不要自行吃退燒藥（衛教單張 ONC-25），需要盡快就醫。",
    "bleeding": "出血的情形需要立即處理（衛教單張 ONC-18），請盡快就醫。",
    "cardiorespiratory": "胸痛、胸悶或呼吸困難需要立即處理。",
    "neuro": "這個狀況可能是神經方面的急症，需要立即處理。",
    "dehydration": "沒有尿、喝不下水或一直吐瀉，可能已經脫水，需要盡快就醫。",
    "allergy": "臉、嘴唇或喉嚨腫、全身起疹子，可能是嚴重過敏反應，需要立即處理。",
    "injury": "化療期間血小板可能偏低，跌倒撞到頭或受傷需要盡快檢查。",
}
DEFAULT_LEAD = "這個狀況需要立即處理。"

# 自傷：同理 + 不讓病患獨自面對 + 24 小時專線（衛福部安心專線 1925）
SELF_HARM_LEAD = ("謝謝您願意告訴我，聽起來您現在真的很辛苦，您不是一個人。"
                  "如果您有傷害自己的想法，請現在就撥打 24 小時安心專線 1925，或撥 119；"
                  "也請讓身邊的家人或朋友知道您現在的感受。")

LINE_NOT_MONITORED = "這裡不是 24 小時有醫護人員即時監看，系統會記錄並提醒醫療團隊，但請不要等待這裡的回覆。"


def emergency_reply(channel: str, category: str, contact: str) -> str:
    """HIGH 紅旗的固定回覆。"""
    if category == "self_harm":
        lead = SELF_HARM_LEAD
    else:
        lead = CATEGORY_LEAD.get(category, DEFAULT_LEAD)
    if channel == "line":
        return f"{lead}\n{contact}。\n{LINE_NOT_MONITORED}"
    return f"{lead}\n請立刻按呼叫鈴讓護理師知道（系統也會提醒護理站，但請以呼叫鈴為主）。"


def escalation_reply(channel: str, symptom_name: str, score, detail: str, level: str, contact: str) -> str:
    """症狀評估達門檻的回覆。level=high 時（CTCAE grade 3）明確建議就醫。"""
    head = f"您的「{symptom_name}」聽起來比較嚴重（嚴重度 {score}/10{detail}）。"
    if level == "high":
        head += "這個程度需要盡快就醫。"
    if channel == "line":
        return f"{head}\n{contact}。\n{LINE_NOT_MONITORED}"
    return f"{head}我已經幫您記錄並提醒護理站，請按呼叫鈴讓護理師知道。"


def deflect_reply(channel: str, contact: str) -> str:
    """知識庫沒有可用來源時的固定轉介（不經 LLM）。"""
    if channel == "line":
        return ("這個問題目前的衛教資料沒有涵蓋，我沒辦法回答。"
                "我會把問題記錄下來供醫療團隊參考，但不保證會有人透過這裡回覆；"
                f"建議您回診時詢問醫護人員。如果身體不舒服或擔心，{contact}。")
    return "這個問題目前的衛教資料沒有涵蓋，我沒辦法回答。請直接詢問您的護理師，或按呼叫鈴。"


def help_text(channel: str, contact: str) -> str:
    """系統錯誤時附加的求助指引。"""
    if channel == "line":
        return f"{contact}。"
    return "請按呼叫鈴通知護理師。"


def safety_rule_for_prompt(channel: str, contact: str) -> str:
    """注入 LLM system prompt 的緊急處置指示（紅旗規則漏接時的第二道防線）。"""
    if channel == "line":
        action = f"「這個症狀需要立即處理，{contact}，請不要等待這裡的回覆」"
    else:
        action = "「這個症狀需要馬上讓護理師或醫師知道，請立刻按呼叫鈴」"
    return action


def export_for_demo(contact: str) -> dict:
    """給 scripts/build_pages.py：演示頁（網頁情境）用的同一份文字。"""
    cats = list(CATEGORY_LEAD) + ["self_harm", "other"]
    return {
        "emergency": {c: emergency_reply("web", c, contact) for c in cats},
        "deflect": deflect_reply("web", contact),
        "escalation_suffix_web": "我已經幫您記錄並提醒護理站，請按呼叫鈴讓護理師知道。",
        "escalation_high_note": "這個程度需要盡快就醫。",
    }
