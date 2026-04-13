"""
緊急關鍵字分級偵測模組
High：立即通知護理師
Medium：標記並記錄
"""
from dataclasses import dataclass, field

EMERGENCY_KEYWORDS = {
    "high": [
        # 心臟/呼吸緊急
        "胸痛", "胸悶", "喘不過氣", "呼吸困難", "心跳很快", "心悸很嚴重",
        # 神經緊急
        "手腳無力", "說話不清楚", "突然頭痛", "暈倒", "失去意識",
        # 自傷/自殺
        "想死", "不想活", "自殺", "傷害自己", "活不下去",
        # 跌倒/外傷
        "跌倒了", "流很多血", "骨折",
        # 嚴重過敏
        "全身起疹子", "臉腫起來", "喉嚨腫",
    ],
    "medium": [
        "很痛", "痛很久", "發高燒", "吐很多次", "一直拉肚子",
        "藥吃錯了", "忘記吃藥", "血糖很高", "血壓很高",
        "傷口紅腫", "尿尿有血",
    ]
}


@dataclass
class AlertResult:
    is_emergency: bool
    severity: str    # "none" | "medium" | "high"
    matched_keywords: list[str] = field(default_factory=list)


def detect_emergency(text: str) -> AlertResult:
    matched_high = [kw for kw in EMERGENCY_KEYWORDS["high"] if kw in text]
    matched_medium = [kw for kw in EMERGENCY_KEYWORDS["medium"] if kw in text]

    if matched_high:
        return AlertResult(True, "high", matched_high)
    elif matched_medium:
        return AlertResult(True, "medium", matched_medium)
    return AlertResult(False, "none", [])
