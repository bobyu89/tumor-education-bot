"""
從 Obsidian_Vault/衛教知識庫/*.md 的「## 護理指導評值」區塊抽出測驗題庫。

輸出 data/quiz_bank.json：每個 ONC 主題含是非題（O/X）與選擇題（1-4）+ 正解。
嚴格模式：題數與答案數對不上、或答案解析不出，即報錯（研究測驗答案不能錯）。

用法：python -X utf8 scripts/build_quiz_bank.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VAULT = ROOT / "Obsidian_Vault" / "衛教知識庫"
# 題庫是衍生自 vault 的靜態內容，該進版控（放 backend/，不放被 gitignore 的 data/）
OUT = ROOT / "backend" / "quiz_bank.json"

# 已知原始 PDF 解答有誤植的人工修正（附理由，非臆測）。
# ONC-21：原件解答選擇題題號誤植為「1.(3) 2.(4) 3.(1)」（應為 4/5/6）。
#         依位置對應且答案值皆為有效選項，修正為 Q4=3、Q5=4、Q6=1。
ANSWER_OVERRIDES = {
    "ONC-21": {1: "O", 2: "X", 3: "O", 4: "3", 5: "4", 6: "1"},
}


def _strip_quote(line: str) -> str:
    return re.sub(r"^>\s?", "", line).rstrip()


def parse_eval_section(md: str) -> tuple[dict, dict]:
    """回傳 (questions_by_n, answers_by_n)。答案配對交由 main 做（可套用修正）。"""
    m = re.search(r"^## 護理指導評值\s*$(.*?)(?=^## |\Z)", md, re.M | re.S)
    if not m:
        return {}, {}
    sec = m.group(1)

    mode = None                      # tf | mc | ans
    questions: dict[int, dict] = {}
    answers: dict[int, str] = {}

    for raw in sec.splitlines():
        line = _strip_quote(raw).strip()
        if not line:
            continue
        if "是非題" in line and line.startswith("[!"):
            mode = "tf"; continue
        if "選擇題" in line and line.startswith("[!"):
            mode = "mc"; continue
        if "解答" in line and line.startswith("[!"):
            mode = "ans"; continue

        if mode in ("tf", "mc"):
            qm = re.match(r"^(\d+)\.\s*[（(]\s*[）)]\s*(.+)$", line)
            if qm:
                n = int(qm.group(1))
                questions[n] = {"n": n, "type": mode, "text": qm.group(2).strip()}
                continue
            # 選項行：(1)... 或 1.... 或 1、...
            if mode == "mc" and re.match(r"^[（(]?1[）)．.、]", line) and questions:
                opts = _parse_options(line)
                if opts:
                    questions[max(questions)]["options"] = opts
                continue

        if mode == "ans":
            answers.update(_parse_answers(line))

    return questions, answers


def _parse_options(line: str) -> list[str]:
    # 以 ；;、換行 切；每項去掉 (N) 前綴
    parts = re.split(r"[；;]", line)
    opts = []
    for p in parts:
        p = p.strip().rstrip("。").strip()
        p = re.sub(r"^[（(]?\d+[）)]\s*", "", p)
        if p:
            opts.append(p)
    return opts


def _parse_answers(line: str) -> dict[int, str]:
    """解析如 '1.(O) 2.（X） 4.(1)' → {1:'O',2:'X',4:'1'}，半/全形皆可。"""
    out = {}
    for m in re.finditer(r"(\d+)\s*\.\s*[（(]\s*([OXＯＸ0-9])\s*[）)]", line):
        n = int(m.group(1))
        a = m.group(2).translate(str.maketrans("ＯＸ", "OX"))
        out[n] = a
    return out


def main():
    bank = {}
    problems = []
    overridden = []
    for path in sorted(VAULT.glob("*.md")):
        md = path.read_text(encoding="utf-8")
        code_m = re.search(r"^code:\s*(\S+)", md, re.M)
        topic_m = re.search(r"^topic:\s*(.+)$", md, re.M)
        code = code_m.group(1) if code_m else path.stem.split()[0]
        topic = topic_m.group(1).strip() if topic_m else ""

        questions, answers = parse_eval_section(md)
        if not questions:
            problems.append((code, "無護理指導評值題目"))
            continue

        # 套用已知修正
        if code in ANSWER_OVERRIDES:
            answers = ANSWER_OVERRIDES[code]
            overridden.append(code)

        # 嚴格配對
        qs, err = [], None
        for n in sorted(questions):
            q = questions[n]
            if n not in answers:
                err = f"第 {n} 題無對應答案"; break
            if q["type"] == "mc" and "options" not in q:
                err = f"選擇題第 {n} 題無選項"; break
            q["answer"] = answers[n]
            qs.append(q)
        if err:
            problems.append((code, err))
            continue
        bank[code] = {"topic": topic, "questions": qs}

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(bank, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"題庫 → {OUT}")
    print(f"成功解析 {len(bank)} 個主題：")
    for code, d in bank.items():
        tf = sum(1 for q in d["questions"] if q["type"] == "tf")
        mc = sum(1 for q in d["questions"] if q["type"] == "mc")
        ans = " ".join(f"{q['n']}.{q['answer']}" for q in d["questions"])
        mark = "（已修正）" if code in overridden else ""
        print(f"  {code} {d['topic']:<12} 是非{tf} 選擇{mc}  解答: {ans} {mark}")
    if overridden:
        print(f"\n人工修正（原件解答誤植）：{', '.join(overridden)}")
    if problems:
        print(f"\n⚠️ {len(problems)} 個主題無法解析：")
        for code, err in problems:
            print(f"  {code}: {err}")


if __name__ == "__main__":
    main()
