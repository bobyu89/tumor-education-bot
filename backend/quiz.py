"""
知識測驗引擎（Phase 3b）。前後測（pre/post）衡量衛教成效。

題庫 backend/quiz_bank.json 由 scripts/build_quiz_bank.py 從 vault 的
「護理指導評值」抽出（是非題 O/X、選擇題 1-4 + 正解）。研究用，答案經嚴格解析與人工校正。

- topics()：可測驗的主題清單
- get_questions(onc_code)：出題（**不含答案**）
- score(patient_code, onc_code, answers, phase)：評分並落 quiz_results
"""
from __future__ import annotations

import json
from pathlib import Path

import db

_BANK_PATH = Path(__file__).resolve().parent / "quiz_bank.json"
_BANK: dict = json.loads(_BANK_PATH.read_text(encoding="utf-8")) if _BANK_PATH.exists() else {}


def topics() -> list[dict]:
    return [{"onc_code": code, "topic": d["topic"], "n_questions": len(d["questions"])}
            for code, d in _BANK.items()]


def get_questions(onc_code: str) -> dict | None:
    """出題：回傳題目與選項，但**移除答案**（避免前端拿到正解）。"""
    d = _BANK.get(onc_code)
    if not d:
        return None
    qs = []
    for q in d["questions"]:
        item = {"n": q["n"], "type": q["type"], "text": q["text"]}
        if q["type"] == "mc":
            item["options"] = q["options"]     # [str, ...]，選項編號 = index+1
        else:
            item["options"] = ["是 (O)", "否 (X)"]
        qs.append(item)
    return {"onc_code": onc_code, "topic": d["topic"], "questions": qs}


def score(patient_code: str, onc_code: str, answers: dict, phase: str) -> dict | None:
    """answers: {題號(str/int): 作答}. 是非題作答 'O'/'X'；選擇題作答 '1'..'4'。

    回傳 {score, total, correct_n, details:[{n, your, answer, correct}]}，並落 quiz_results。
    """
    d = _BANK.get(onc_code)
    if not d:
        return None
    if phase not in ("pre", "post"):
        raise ValueError("phase 必須是 pre 或 post")

    # 正規化 key 為字串
    ans = {str(k): str(v).strip().upper() for k, v in answers.items()}
    details, correct_n = [], 0
    for q in d["questions"]:
        your = ans.get(str(q["n"]), "")
        ok = (your == str(q["answer"]).upper())
        if ok:
            correct_n += 1
        details.append({"n": q["n"], "your": your, "answer": q["answer"], "correct": ok})

    total = len(d["questions"])
    db.add_quiz_result(patient_code, onc_code, phase, correct_n)
    db.log_event("quiz_submit", patient_code,
                 {"onc_code": onc_code, "phase": phase, "score": correct_n, "total": total})
    return {"onc_code": onc_code, "phase": phase, "score": correct_n,
            "total": total, "correct_n": correct_n, "details": details}
