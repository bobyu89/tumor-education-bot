"""
紅旗偵測 v2（取代舊 alert.py）。

v1 的問題（2026-11 同儕審查，三位審查者獨立實測）：
  - 發燒完全偵測不到（只有「發高燒」且列 MEDIUM）—— 化療 nadir 期發燒可能是
    febrile neutropenia（Taplitz et al., J Clin Oncol 2018），屬腫瘤急症。
  - 否定詞含單字「沒／無／怕」、往前看 8 字且不分子句 → 「我昨天沒睡好今天胸痛」被壓掉。
  - 「沒有意義」「吞不下」「跌倒了」造成大量誤報（「這樣漱口有沒有意義」→ 叫病人打 119）。
  - 出血、脫水、過敏反應、台語說法幾乎沒有涵蓋。

v2 設計（安全底線仍是確定性規則，不經 LLM）：
  1. 正規化：NFKC（全形數字→半形）、去空白。
  2. 子句切分：標點與轉折詞（但／可是／只是／不過／然後／結果）處切開，
     否定與假設語境只在「同一子句」內生效。
  3. 否定：否定詞必須「緊貼」關鍵字（中間最多夾「有／再／什麼／很／太…」等填充字），
     例：「沒有胸痛」「沒什麼發燒」「不會喘」被否定；「沒睡好今天胸痛」不被否定。
  4. 假設／預防語境：同一子句內關鍵字前出現「如果／萬一／預防／避免…」→ 不觸發。
  5. 體溫數值判讀：門檻依科別設定（knowledge/<科別>/manifest.json）。血液腫瘤科 ≥38.0°C → HIGH
     （對齊 ONC-22「發燒超過 38°C 含以上」、ONC-25「發燒不可自行吃退燒藥」）、37.5–37.9 → MEDIUM。
  8. 規則分兩層：通用核心（所有科別）＋科別擴充（發燒等級、體溫門檻、額外規則），見 RedFlagProfile。
  6. 自傷類一律不套否定與假設（寧可誤報，不可漏接），但移除語意過寬的詞。
  7. 向量補充只加分不減分；載入失敗時關鍵字底線仍在。

⚠️ 詞庫與門檻是「開發者依衛教單張與審查意見整理」的版本，正式上線前必須由
   臨床人員審核，並以獨立撰寫的 held-out 測試集驗證（見 docs/specs 與計畫書）。
   開發用回歸測試集：backend/redflag_cases.json（Python 與演示引擎共用）。
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

# ── 規則表 ────────────────────────────────────────────────────────
# 每條規則：(類別, 關鍵字或 re 樣式)。以 "re:" 開頭者為正規表示式。
# 類別決定給病患的固定回覆（見 chat_service.emergency_reply）與護理端顯示。

HIGH_SELF_HARM = [
    # 不套否定／假設（「不想活」本身就含「不」）
    "想死", "不想活", "活不下去", "自殺", "傷害自己", "結束生命", "撐不下去", "活著好累",
    "活著沒有意義", "活著沒意義", "活著沒意思", "活著沒有意思", "人生沒有意義", "活著幹嘛",
    "不如死了", "死了算了", "跳樓", "跳下去",
    "一了百了", "想不開", "輕生", "了結自己", "不想醒來", "想消失",
    "安眠藥都存", "存安眠藥", "存了安眠藥",
    # 台語書寫
    "想欲死", "我欲死", "毋想活", "袂活落去",
]

# 發燒／感染詞：嚴重度由科別決定（RedFlagProfile.fever_level）。
# 化療科別（白血球低下期發燒可能是 febrile neutropenia）設 high；其他科別預設 medium。
FEVER_TERMS: list[tuple[str, str]] = [
    ("fever", "發燒"), ("fever", "高燒"), ("fever", "燒到"), ("fever", "有燒"),
    ("fever", "燒起來"), ("fever", "燒燒"), ("fever", "畏寒"),
    ("fever", "寒顫"), ("fever", "發冷"), ("fever", "冷到發抖"), ("fever", "打冷顫"),
    ("fever", "退燒藥"),
    ("fever", "re:人工血管.{0,6}(紅腫|流膿).{0,6}(燒|熱)"),
]

# 通用核心：所有科別都適用的急症
HIGH_PHYSICAL: list[tuple[str, str]] = [
    # 心肺
    ("cardiorespiratory", "胸痛"), ("cardiorespiratory", "胸悶"),
    ("cardiorespiratory", "胸口痛"), ("cardiorespiratory", "胸口悶"),
    ("cardiorespiratory", "胸口很痛"), ("cardiorespiratory", "胸口好痛"),
    ("cardiorespiratory", "re:胸口.{0,2}(悶|痛)"),
    ("cardiorespiratory", "心口痛"), ("cardiorespiratory", "胸坎痛"),
    ("cardiorespiratory", "喘不過氣"), ("cardiorespiratory", "喘不過來"),
    ("cardiorespiratory", "喘不上來"), ("cardiorespiratory", "喘袂過"),
    ("cardiorespiratory", "喘到"), ("cardiorespiratory", "很喘"), ("cardiorespiratory", "好喘"),
    ("cardiorespiratory", "re:呼吸.{0,3}困難"), ("cardiorespiratory", "不能呼吸"),
    ("cardiorespiratory", "沒辦法呼吸"), ("cardiorespiratory", "吸不到氣"),
    ("cardiorespiratory", "心跳很快"), ("cardiorespiratory", "心跳好快"),
    ("cardiorespiratory", "心悸很嚴重"), ("cardiorespiratory", "嘴唇發紫"),
    ("cardiorespiratory", "冒冷汗"),
    # 神經
    ("neuro", "手腳無力"), ("neuro", "半邊無力"), ("neuro", "單邊無力"),
    ("neuro", "說話不清楚"), ("neuro", "講話不清楚"), ("neuro", "嘴歪"),
    ("neuro", "突然頭痛"), ("neuro", "劇烈頭痛"), ("neuro", "頭痛欲裂"),
    ("neuro", "暈倒"), ("neuro", "昏倒"), ("neuro", "失去意識"), ("neuro", "叫不醒"),
    ("neuro", "意識不清"), ("neuro", "抽搐"),
    # 出血（ONC-18：出血持續不止、劇烈頭痛嘔吐 → 立即就醫）
    ("bleeding", "流很多血"), ("bleeding", "大量出血"), ("bleeding", "血流不止"),
    ("bleeding", "流血不止"), ("bleeding", "re:血.{0,4}止不住"), ("bleeding", "吐血"),
    ("bleeding", "咳血"), ("bleeding", "黑便"), ("bleeding", "柏油"),
    ("bleeding", "re:大便.{0,3}黑"), ("bleeding", "re:便便.{0,3}黑"),
    ("bleeding", "大量血便"), ("bleeding", "一直血便"), ("bleeding", "馬桶都是血"),
    ("bleeding", "re:流血.{0,4}停不(下來|住)"),
    # 脫水／無法進食飲水
    ("dehydration", "沒有尿"), ("dehydration", "沒尿"), ("dehydration", "尿不出來"),
    ("dehydration", "喝不下水"), ("dehydration", "喝水都吐"), ("dehydration", "吃什麼都吐"),
    ("dehydration", "水瀉不止"), ("dehydration", "站起來就暈"),
    ("dehydration", "re:暈.{0,4}站不(起來|穩)"),
    # 過敏反應
    ("allergy", "全身起疹子"), ("allergy", "全身起紅疹"), ("allergy", "全身蕁麻疹"),
    ("allergy", "re:(臉|嘴唇|舌頭|喉嚨|眼皮).{0,3}腫"), ("allergy", "re:喉嚨.{0,2}緊"),
    ("allergy", "口水吞不下"),
    ("allergy", "口水都吞不下"),
    # 外傷
    ("injury", "骨折"), ("injury", "撞到頭"), ("injury", "頭撞到"),
    ("injury", "re:跌倒.{0,4}頭"),
]

MEDIUM: list[tuple[str, str]] = [
    ("symptom", "很痛"), ("symptom", "痛很久"), ("symptom", "吐很多次"), ("symptom", "一直吐"),
    ("symptom", "一直拉肚子"), ("symptom", "頭暈"), ("symptom", "會喘"),
    ("medication", "藥吃錯了"), ("medication", "忘記吃藥"),
    ("vital", "血糖很高"), ("vital", "血壓很高"),
    ("infection", "傷口紅腫"), ("infection", "re:人工血管.{0,6}(紅|腫|痛|流膿|滲)"),
    ("bleeding", "尿尿有血"), ("bleeding", "尿血"), ("bleeding", "血便"),
    ("bleeding", "流鼻血"), ("bleeding", "牙齦流血"), ("bleeding", "瘀青"),
    ("injury", "跌倒"),
    ("swallowing", "吞不下"),
]

# 否定詞（必須緊貼關鍵字，中間只允許填充字）
_NEG_CUES = ["沒有", "並沒有", "沒", "無", "不會", "不再", "未曾", "從未", "並未", "未", "不"]
_FILLERS = ["什麼", "怎麼", "有", "再", "很", "太", "在", "到", "超過", "那麼", "那", "會", "持續"]
# 假設／預防語境（同一子句、關鍵字之前任意位置）
_HYPOTHETICAL = ["如果", "假如", "萬一", "要是", "假設", "會不會", "是否會", "預防", "避免",
                 "擔心會", "怕會", "以免", "怎樣算", "什麼情況"]
# 子句切分：標點與轉折詞。不用「不過」：會把「喘不過氣」切斷（轉折的「不過」多半已在標點後）
_CLAUSE_SPLIT = re.compile(r"[,，。!！?？;；、\n~～]|(?<!\d)\.|\.(?!\d)|(?=但是|可是|只是|然後|結果)|(?<!不)(?=但)")
# 體溫：環境溫度語境不算
_AMBIENT = ["天氣", "氣溫", "外面", "室溫", "水溫", "洗澡", "熱水", "冷氣", "溫度計壞"]
_TEMP_RE = re.compile(r"(\d{2}(?:\.\d+)?)\s*(?:度|°|℃|c\b)")
_TEMP_CTX_RE = re.compile(r"(?:體溫|溫度|耳溫|額溫|口溫|腋溫|量|燒到|發燒)[^\d]{0,3}(\d{2}(?:\.\d+)?)")
_ZH_TEMP_RE = re.compile(r"(四十[一二]?|三十[七八九])度?(半|點五)?")
_ZH_NUM = {"三十七": 37, "三十八": 38, "三十九": 39, "四十": 40, "四十一": 41, "四十二": 42}

_SEVERITY_ORDER = {"none": 0, "medium": 1, "high": 2}


@dataclass(frozen=True)
class RedFlagProfile:
    """科別的紅旗設定（來自 knowledge/<科別>/manifest.json 的 redflag 區塊）。

    fever_level  發燒詞的嚴重度：high | medium
    temp_high    體溫 ≥ 此值判 HIGH；None = 體溫不會單獨判 HIGH
    temp_medium  體溫 ≥ 此值判 MEDIUM；None = 不判
    extra_high / extra_medium  科別專屬規則，格式同 HIGH_PHYSICAL：[[類別, 關鍵字或 re:樣式], …]
    """
    fever_level: str = "medium"
    temp_high: float | None = None
    temp_medium: float | None = 38.0
    extra_high: tuple = ()
    extra_medium: tuple = ()

    @classmethod
    def from_dict(cls, d: dict | None) -> "RedFlagProfile":
        d = d or {}
        level = d.get("fever_level", "medium")
        if level not in ("high", "medium"):
            raise ValueError(f"redflag.fever_level 只能是 high 或 medium：{level}")
        return cls(fever_level=level, temp_high=d.get("temp_high"), temp_medium=d.get("temp_medium", 38.0),
                   extra_high=tuple(tuple(r) for r in d.get("extra_high", [])),
                   extra_medium=tuple(tuple(r) for r in d.get("extra_medium", [])))

    def rules(self) -> tuple[list, list]:
        """合併後的 (HIGH 規則, MEDIUM 規則)：通用核心＋發燒詞（依等級）＋科別擴充。"""
        high = list(HIGH_PHYSICAL) + [tuple(r) for r in self.extra_high]
        medium = list(MEDIUM) + [tuple(r) for r in self.extra_medium]
        (high if self.fever_level == "high" else medium)[:0] = FEVER_TERMS
        return high, medium


# 通用預設（未指定科別、也讀不到科別設定時）：發燒為 MEDIUM，體溫 ≥38 為 MEDIUM
CORE_PROFILE = RedFlagProfile()


def profile_for(dept_id: str | None = None) -> RedFlagProfile:
    """依科別取得紅旗設定；未指定時用預設科別。"""
    import departments
    return RedFlagProfile.from_dict(departments.get(dept_id).redflag)


@dataclass
class RedFlagResult:
    is_emergency: bool
    severity: str                       # "none" | "medium" | "high"
    matched_keywords: list[str] = field(default_factory=list)
    via: str = "keyword"                # "keyword" | "vector" | "none"
    category: str = "none"              # fever | cardiorespiratory | ... | self_harm | none

    # 與舊 AlertResult 相容
    @property
    def is_high(self) -> bool:
        return self.severity == "high"


# ── 工具 ──────────────────────────────────────────────────────────
def normalize(text: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text or "")).lower()


def clauses(text: str) -> list[str]:
    return [c for c in _CLAUSE_SPLIT.split(text) if c]


def _find(clause: str, pattern: str) -> list[int]:
    """回傳 pattern 在子句中每次出現的起始位置。"""
    if pattern.startswith("re:"):
        return [m.start() for m in re.finditer(pattern[3:], clause)]
    out, i = [], clause.find(pattern)
    while i != -1:
        out.append(i)
        i = clause.find(pattern, i + 1)
    return out


def _label(pattern: str, clause: str, pos: int) -> str:
    if pattern.startswith("re:"):
        return re.compile(pattern[3:]).match(clause, pos).group(0)
    return pattern


def _immediately_negated(prefix: str) -> bool:
    """prefix 是關鍵字前方（同子句）的文字。去掉尾端填充字後，是否以否定詞結尾。"""
    p = prefix
    changed = True
    while changed and p:
        changed = False
        for f in _FILLERS:
            if p.endswith(f):
                p = p[: -len(f)]
                changed = True
    return any(p.endswith(n) for n in _NEG_CUES)


def _suppressed(clause: str, pos: int) -> bool:
    prefix = clause[:pos]
    if any(h in prefix for h in _HYPOTHETICAL):
        return True
    return _immediately_negated(prefix)


def _match_rules(clause_list: list[str], rules: list[tuple[str, str]]) -> list[tuple[str, str]]:
    hits = []
    for cat, pat in rules:
        for cl in clause_list:
            hit = next((p for p in _find(cl, pat) if not _suppressed(cl, p)), None)
            if hit is not None:
                hits.append((cat, _label(pat, cl, hit)))
                break
    return hits


def parse_temperature(clause: str) -> float | None:
    """抓子句中的體溫數值（35–43°C）；環境溫度語境回傳 None。"""
    if any(a in clause for a in _AMBIENT):
        return None
    vals = [float(m.group(1)) for m in _TEMP_RE.finditer(clause)]
    vals += [float(m.group(1)) for m in _TEMP_CTX_RE.finditer(clause)]
    if any(k in clause for k in ("度", "燒", "溫")):
        vals += [_ZH_NUM[m.group(1)] + (0.5 if m.group(2) else 0) for m in _ZH_TEMP_RE.finditer(clause)]
    vals = [v for v in vals if 35.0 <= v <= 43.0]
    return max(vals) if vals else None


def _temperature_screen(clause_list: list[str], profile: RedFlagProfile) -> tuple[str, str] | None:
    best = None
    for cl in clause_list:
        t = parse_temperature(cl)
        if t is None:
            continue
        # 「沒有到 38 度」「不到38度」視為否定
        m = re.search(r"\d|三十|四十", cl)
        if m and _immediately_negated(cl[: m.start()]):
            continue
        if any(h in cl for h in _HYPOTHETICAL):
            continue
        if best is None or t > best:
            best = t
    if best is None:
        return None
    if profile.temp_high is not None and best >= profile.temp_high:
        return ("high", f"體溫{best:g}°C")
    if profile.temp_medium is not None and best >= profile.temp_medium:
        return ("medium", f"體溫{best:g}°C")
    return None


# ── 關鍵字層 ──────────────────────────────────────────────────────
def _keyword_screen(text: str, profile: RedFlagProfile | None = None) -> RedFlagResult:
    profile = profile or profile_for()
    high_rules, medium_rules = profile.rules()
    norm = normalize(text)
    cl = clauses(norm)

    # 自傷：整句比對，不套否定／假設
    self_harm = [kw for kw in HIGH_SELF_HARM if kw in norm]
    if self_harm:
        return RedFlagResult(True, "high", self_harm, "keyword", "self_harm")

    temp = _temperature_screen(cl, profile)
    high = _match_rules(cl, high_rules)
    if temp and temp[0] == "high":
        high.insert(0, ("fever", temp[1]))
    if high:
        terms = list(dict.fromkeys(t for _, t in high))
        return RedFlagResult(True, "high", terms, "keyword", high[0][0])

    med = _match_rules(cl, medium_rules)
    if temp and temp[0] == "medium":
        med.insert(0, ("fever", temp[1]))
    if med:
        terms = list(dict.fromkeys(t for _, t in med))
        return RedFlagResult(True, "medium", terms, "keyword", med[0][0])

    return RedFlagResult(False, "none", [], "none", "none")


# ── 向量補充（惰性載入，失敗則優雅降級）──────────────────────────
_VECTOR_EXEMPLARS = {
    "high": [
        "我覺得我快不行了", "喘不上來很難受", "整個人快昏過去",
        "我真的撐不住了想結束這一切", "活著好痛苦不想再撐了",
        "燒得很厲害退不下來", "全身發冷一直發抖", "大便黑黑的像柏油一樣", "一直流血止不住",
    ],
    "medium": [
        "吐到整個人虛脫", "肚子絞痛拉個不停",
    ],
}
# cosine 相似度門檻（paraphrase-multilingual-MiniLM-L12-v2，見 embedding.REDFLAG_MODEL）。MiniLM 對中文短句普遍給出高相似度：0.72 時「我手腳麻麻的」「一直想吐」
# 「嘴巴破了好痛」都會被升級（20 句常見症狀描述有 6 句誤報）；0.85 時為 0。
# 原本只靠向量才抓到的急症說法（流血停不下來、冒冷汗）已改由關鍵字負責。見 test_redflag.py 的向量檢查。
_VECTOR_THRESHOLD = 0.85

_embedder = None
_exemplar_vecs = None


def _load_vectors():
    global _embedder, _exemplar_vecs
    if _embedder is not None:
        return
    try:
        import numpy as np
        from embedding import REDFLAG_MODEL, make_embedding_function
        # 固定用門檻校準時的模型，不跟著檢索模型（EMBED_MODEL）換
        ef = make_embedding_function(REDFLAG_MODEL)
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
            return RedFlagResult(True, level, [], "vector", "vector")
    return None


# ── 主入口 ────────────────────────────────────────────────────────
def screen(text: str, use_vector: bool = True, dept: str | None = None) -> RedFlagResult:
    """關鍵字為底；未命中 HIGH 且啟用向量時，用向量補一層。取較嚴重者。
    dept：病患所屬科別（決定發燒等級、體溫門檻與科別擴充規則）；未指定用預設科別。"""
    kw = _keyword_screen(text, profile_for(dept))
    if kw.severity == "high" or not use_vector:
        return kw
    vec = _vector_screen(text)
    if vec is None:
        return kw
    return vec if _SEVERITY_ORDER[vec.severity] > _SEVERITY_ORDER[kw.severity] else kw


# 與舊 API 相容的別名
def detect_emergency(text: str) -> RedFlagResult:
    return screen(text)
