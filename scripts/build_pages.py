"""
產生 GitHub Pages 靜態演示頁的資料檔 docs/demo_data.js。

來源：
  Obsidian_Vault/衛教知識庫/ONC-*.md   → 13 份單張的章節全文（排除「參考資料」「護理指導評值」）
  backend/quiz_bank.json               → 知識測驗題庫（靜態頁在瀏覽器內評分，正解會在原始碼中，僅供演示）
  本檔內的 PATIENTS / SEED             → 與 scripts/seed_demo.py 對應的 5 位模擬病患與歷程摘要

執行（專案根目錄）：python -X utf8 scripts/build_pages.py
頁面本身是 docs/index.html + docs/demo_engine.js（純前端，無後端、無 LLM）。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VAULT = ROOT / "Obsidian_Vault" / "衛教知識庫"
OUT = ROOT / "docs" / "demo_data.js"
EXCLUDE_SECTIONS = ("參考資料", "護理指導評值")

# 檢索用同義關鍵字（語意檢索的靜態替代：命中越多、越像該單張）
KEYWORDS = {
    "ONC-17": ["嘴巴破", "口腔", "潰瘍", "漱口", "黏膜", "嘴破", "口破"],
    "ONC-18": ["血小板", "出血", "瘀青", "流血", "刷牙", "牙齦", "止血"],
    "ONC-19": ["紅血球", "貧血", "血紅素", "頭暈", "鐵質", "含鐵", "臉色"],
    "ONC-21": ["噁心", "嘔吐", "想吐", "食慾", "吃不下", "反胃", "胃口", "止吐"],
    "ONC-22": ["腹瀉", "拉肚子", "水便", "大便", "脫水"],
    "ONC-23": ["便祕", "便秘", "排便", "大不出來", "解不出來", "軟便"],
    "ONC-24": ["掉髮", "落髮", "頭髮", "毛髮", "假髮", "掉頭髮", "禿"],
    "ONC-25": ["白血球", "嗜中性", "感染", "發燒", "生食", "口罩", "洗手", "人多"],
    "ONC-35": ["穴位", "內關", "按摩", "穴道"],
    "ONC-37": ["疲憊", "很累", "累", "沒力氣", "體力", "睡眠", "睡不好", "倦怠"],
    "ONC-38": ["手麻", "腳麻", "麻木", "刺痛", "神經", "麻麻", "燙傷"],
    "ONC-39": ["過敏", "蕁麻疹", "藥物反應", "起疹", "發癢"],
    "ONC-40": ["紅疹", "疹子", "皮膚", "乾燥", "癢", "防曬"],
}

PATIENTS = [
    dict(code="P001", name="陳先生（模擬）", age=58, gender="男", cancer="口腔癌",
         meds=["Cisplatin", "5-FU"], tone="general", note="一般語氣"),
    dict(code="P002", name="王女士（模擬）", age=72, gender="女", cancer="大腸癌",
         meds=["Oxaliplatin", "5-FU", "Leucovorin"], tone="simple", note="長者語氣：短句、少重點"),
    dict(code="P003", name="林小姐（模擬）", age=45, gender="女", cancer="乳癌",
         meds=["Doxorubicin", "Cyclophosphamide", "Paclitaxel"], tone="general", note="一般語氣"),
    dict(code="P004", name="張先生（模擬）", age=63, gender="男", cancer="肺癌",
         meds=["Carboplatin", "Paclitaxel"], tone="detailed", note="詳細語氣：可用術語、多重點"),
    dict(code="P005", name="黃先生（模擬）", age=35, gender="男", cancer="淋巴瘤",
         meds=["R-CHOP"], tone="general", note="一般語氣"),
]

# 與 seed_demo.py 對應的歷程摘要（讓儀表板一打開就有內容）
SEED = {
    "scores": [
        dict(code="P001", symptom="mucositis", score=4, extra={}, daysAgo=12),
        dict(code="P002", symptom="diarrhea", score=5, extra={"count": 5}, daysAgo=10),
        dict(code="P002", symptom="diarrhea", score=3, extra={"count": 3}, daysAgo=7),
        dict(code="P002", symptom="diarrhea", score=2, extra={"count": 2}, daysAgo=3),
        dict(code="P002", symptom="neuropathy", score=4, extra={}, daysAgo=2),
        dict(code="P003", symptom="hair_loss", score=8, extra={}, daysAgo=9),
        dict(code="P003", symptom="nausea", score=8, extra={"count": 6}, daysAgo=6),
        dict(code="P003", symptom="nausea", score=6, extra={"count": 3}, daysAgo=4),
        dict(code="P003", symptom="nausea", score=3, extra={"count": 1}, daysAgo=1),
        dict(code="P005", symptom="fatigue", score=7, extra={}, daysAgo=6),
        dict(code="P005", symptom="fatigue", score=6, extra={}, daysAgo=3),
        dict(code="P005", symptom="fatigue", score=5, extra={}, daysAgo=1),
    ],
    "alerts": [
        dict(code="P002", trigger="一直拉肚子", level="medium", daysAgo=10, ack="R001"),
        dict(code="P003", trigger="nausea:8", level="medium", daysAgo=6, ack="R001"),
        dict(code="P004", trigger="發高燒", level="medium", daysAgo=5, ack="R001"),
        dict(code="P004", trigger="胸痛；喘不過氣", level="high", daysAgo=2, ack="R001"),
        dict(code="P005", trigger="撐不下去", level="high", daysAgo=4, ack="R001"),
    ],
    "quiz": [
        dict(code="P001", onc="ONC-17", phase="pre", score=3), dict(code="P001", onc="ONC-17", phase="post", score=5),
        dict(code="P002", onc="ONC-22", phase="pre", score=2), dict(code="P002", onc="ONC-22", phase="post", score=5),
        dict(code="P003", onc="ONC-21", phase="pre", score=3), dict(code="P003", onc="ONC-21", phase="post", score=6),
        dict(code="P004", onc="ONC-25", phase="pre", score=4), dict(code="P004", onc="ONC-25", phase="post", score=6),
        dict(code="P005", onc="ONC-37", phase="pre", score=2), dict(code="P005", onc="ONC-37", phase="post", score=4),
    ],
    "quality": {"grounded": 10, "assessment": 10, "assessment_escalated": 1,
                "deflected_no_source": 1, "redflag_shortcut": 2},
    "messages": {"P001": 6, "P002": 16, "P003": 12, "P004": 6, "P005": 8},
    "lastMessage": {
        "P001": "漱口水可以用市面上有酒精的那種嗎？",
        "P002": "手指麻麻的，碰到冰的東西會刺痛",
        "P003": "換了止吐藥之後好很多，想問吃東西要注意什麼",
        "P004": "我現在胸痛而且喘不過氣",
        "P005": "昨天謝謝護理師來看我。我還是很累沒力氣，想知道怎麼辦",
    },
}


# ── Markdown 清理 ─────────────────────────────────────────────────
def _clean_line(line: str) -> str | None:
    s = line.rstrip()
    if not s or s == "---":
        return None
    if s.startswith("![[") or s.startswith("!["):          # 圖片
        return None
    if re.match(r"^>\s*\[!\w+\]", s):                        # callout 標頭行
        return None
    s = re.sub(r"^>\s?", "", s)                              # callout 內文
    s = re.sub(r"^\s*(?:[-*]|\d+\.)\s+", "", s)             # 清單符號
    s = re.sub(r"\[\[([^\]|]+)\|([^\]]+)\]\]", r"\2", s)
    s = re.sub(r"\[\[([^\]]+)\]\]", r"\1", s)
    s = s.replace("==", "").replace("**", "").replace("`", "")
    s = re.sub(r"^\s*[（(][一二三四五六七八九十\d]+[）)]\s*", "", s)
    s = s.strip()
    return s or None


def parse_leaflet(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    fm, body = {}, text
    if text.startswith("---"):
        _, fm_text, body = text.split("---", 2)
        for line in fm_text.splitlines():
            if ":" in line and not line.startswith(" "):
                k, v = line.split(":", 1)
                fm[k.strip()] = v.strip().strip('"')
    code = fm.get("code", path.stem.split()[0])
    topic = fm.get("topic", path.stem.split(maxsplit=1)[-1])
    title = fm.get("title", topic)

    abstract = ""
    m = re.search(r">\s*\[!abstract\][^\n]*\n((?:>.*\n?)+)", body)
    if m:
        abstract = " ".join(filter(None, (_clean_line(l) for l in m.group(1).splitlines())))

    sections = []
    cur_title, cur_lines = None, []
    for line in body.splitlines():
        if line.startswith("## "):
            if cur_title:
                sections.append((cur_title, cur_lines))
            cur_title, cur_lines = line[3:].strip(), []
        elif cur_title:
            cur_lines.append(line)
    if cur_title:
        sections.append((cur_title, cur_lines))

    out_sections = []
    for t, lines in sections:
        if any(t.startswith(x) for x in EXCLUDE_SECTIONS):
            continue
        t = re.sub(r"^[一二三四五六七八九十]+、", "", t)
        cleaned = [c for c in (_clean_line(l) for l in lines) if c]
        if cleaned:
            out_sections.append({"title": t, "text": "\n".join(cleaned)})

    return {"code": code, "topic": topic, "title": title, "abstract": abstract,
            "keywords": KEYWORDS.get(code, []) + [topic], "sections": out_sections}


def main():
    leaflets = [parse_leaflet(p) for p in sorted(VAULT.glob("ONC-*.md"))]
    quiz = json.loads((ROOT / "backend" / "quiz_bank.json").read_text(encoding="utf-8"))
    data = {"leaflets": leaflets, "quiz": quiz, "patients": PATIENTS, "seed": SEED,
            "builtFrom": "Obsidian_Vault/衛教知識庫 + backend/quiz_bank.json"}
    js = ("// 由 scripts/build_pages.py 產生，請勿手改。\n"
          "(function (root) {\n  var DEMO_DATA = " + json.dumps(data, ensure_ascii=False, indent=1) +
          ";\n  if (typeof module !== 'undefined' && module.exports) module.exports = DEMO_DATA;\n"
          "  else root.DEMO_DATA = DEMO_DATA;\n})(typeof window !== 'undefined' ? window : this);\n")
    OUT.write_text(js, encoding="utf-8")
    n_sec = sum(len(l["sections"]) for l in leaflets)
    print(f"[pages] {len(leaflets)} 份單張、{n_sec} 個章節、{len(quiz)} 組測驗 → {OUT} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
