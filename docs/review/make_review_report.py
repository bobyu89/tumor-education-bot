# -*- coding: utf-8 -*-
"""產生「模擬同儕審查與修正報告」docx（python-docx）。

執行：python3 docs/review/make_review_report.py docs/review/同儕審查與修正報告_v1.1.docx
"""
import re
import sys

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

OUT = sys.argv[1]
CJK, LAT = "Microsoft JhengHei", "Calibri"
TEAL = RGBColor(0x00, 0x6B, 0x5F)
RED = RGBColor(0xB7, 0x1C, 0x1C)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)

doc = Document()
sec = doc.sections[0]
sec.page_height, sec.page_width = Cm(29.7), Cm(21.0)
sec.left_margin = sec.right_margin = Cm(2.2)
sec.top_margin = sec.bottom_margin = Cm(2.0)

st = doc.styles["Normal"]
st.font.name = LAT
st.font.size = Pt(11)
st.element.rPr.rFonts.set(qn("w:eastAsia"), CJK)
st.paragraph_format.line_spacing = 1.3
st.paragraph_format.space_after = Pt(4)
for lvl, size in ((1, 15), (2, 12.5)):
    hs = doc.styles[f"Heading {lvl}"]
    hs.font.name = LAT
    hs.font.size = Pt(size)
    hs.font.color.rgb = TEAL
    hs.element.rPr.rFonts.set(qn("w:eastAsia"), CJK)


def font(run, size=None, bold=None, color=None, italic=None):
    run.font.name = LAT
    rpr = run._element.get_or_add_rPr()
    rf = rpr.find(qn("w:rFonts"))
    if rf is None:
        rf = OxmlElement("w:rFonts")
        rpr.insert(0, rf)
    rf.set(qn("w:eastAsia"), CJK)
    if size:
        run.font.size = Pt(size)
    if bold is not None:
        run.bold = bold
    if italic is not None:
        run.italic = italic
    if color is not None:
        run.font.color.rgb = color


def rich(p, text, size=None, color=None):
    """**粗體**、【待確認…】紅字、*斜體*（參考文獻用）。反引號只是撰寫時標示檔名，輸出時移除。"""
    text = text.replace("`", "")
    for part in re.split(r"(\*\*.+?\*\*|【待[^】]*】|(?<!\*)\*[^*]+\*(?!\*))", text):
        if not part:
            continue
        if part.startswith("**"):
            font(p.add_run(part[2:-2]), size, True, color)
        elif part.startswith("【待"):
            font(p.add_run(part), size, True, RED)
        elif part.startswith("*") and part.endswith("*"):
            font(p.add_run(part[1:-1]), size, None, color, italic=True)
        else:
            font(p.add_run(part), size, None, color)
    return p


def H(text, lvl=1):
    p = doc.add_heading(level=lvl)
    font(p.add_run(text), color=TEAL)


def P(text, size=None, color=None, align=None):
    p = rich(doc.add_paragraph(), text, size, color)
    if align:
        p.alignment = align
    return p


def B(items, style="List Bullet"):
    for it in items:
        rich(doc.add_paragraph(style=style), it)


def shade(cell, hexcolor):
    tcpr = cell._element.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hexcolor)
    tcpr.append(shd)


STATUS_FILL = {"已修正": "E3F1EA", "已決定": "E3F1EA", "部分": "FFF4E0", "待評估": "FFF4E0", "未處理": "FBE9E9", "需決定": "FBE9E9"}


def T(header, rows, widths, size=9.5, status_col=None):
    t = doc.add_table(rows=1, cols=len(header))
    t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    t.autofit = False
    for gc, w in zip(t._tbl.tblGrid.findall(qn("w:gridCol")), widths):
        gc.set(qn("w:w"), str(int(Cm(w).twips)))
    for i, h in enumerate(header):
        c = t.rows[0].cells[i]
        c.text = ""
        font(c.paragraphs[0].add_run(h), size, True, WHITE)
        shade(c, "006B5F")
    for r in rows:
        cells = t.add_row().cells
        for i, v in enumerate(r):
            cells[i].text = ""
            rich(cells[i].paragraphs[0], str(v), size)
            if status_col is not None and i == status_col:
                for k, fill in STATUS_FILL.items():
                    if str(v).startswith(k):
                        shade(cells[i], fill)
    for row in t.rows:
        for i, w in enumerate(widths):
            row.cells[i].width = Cm(w)
    doc.add_paragraph()


# ═══════════════════════ 封面 ═══════════════════════
P("討論用文件", 11, TEAL, WD_ALIGN_PARAGRAPH.CENTER)
p = doc.add_paragraph()
p.alignment = WD_ALIGN_PARAGRAPH.CENTER
font(p.add_run("腫瘤衛教機器人\n模擬同儕審查與修正報告"), 18, True)
P("系統安全性、知識庫適切性與設計論文計畫之跨領域審查，以及第一輪修正結果", 11,
  align=WD_ALIGN_PARAGRAPH.CENTER)
doc.add_paragraph()
T(["項目", "內容"], [
    ["版本／日期", "v1.1／2026-11-25（v1.0 後新增：知識庫與 LLM 兩項決定、Claude 串接修改）"],
    ["用途", "與碩士論文研究生討論系統現況、IRB 回覆素材與後續分工"],
    ["審查對象", "repo `bobyu89/tumor-education-bot`（後端、前端、知識庫）＋設計論文研究計畫書 v0.1"],
    ["修正版本", "分支 ccr-7e03a287-h1n6by（PR #1）"],
    ["標示", "【待確認】＝需要研究團隊或臨床端決定的項目"],
], [3.5, 13])

# ═══════════════════════ 0 先講結論 ═══════════════════════
H("先講結論")
B([
    "**系統目前仍不可以讓真實病人使用。** 三位審查者都認為架構方向正確（安全判斷交給確定性規則、LLM 只負責表述），"
    "但實作有致命缺口：化療後最危險的「發燒」完全偵測不到，系統還對病人宣稱「已通知醫療團隊」，實際上沒有人收到。",
    "**最急的兩項已修正**（紅旗偵測、通知與確認機制），全部測試通過。半獨立測試中，應判 HIGH 的急症句"
    "由 2/26 提升到 21/26（補強後 25/26），誤報由 8/9 降到 3/9。",
    "**已確定的兩項決策**：知識庫以院內現行 13 份衛教單張為準，不修改單張內容，範圍外的問題由系統轉介；"
    "正式收案 LLM 採 Anthropic Claude Sonnet 5.5 或 Opus 5.5（程式已支援，二擇一由設計論文的評估決定）。",
    "本報告第 6 節整理了可以直接用來回覆 IRB 委員的素材。",
])

# ═══════════════════════ 1 審查方式 ═══════════════════════
H("1. 審查方式與限制")
P("本次審查由 AI 模擬三位不同領域的審查委員，各自**獨立**閱讀程式碼、知識庫、計畫書與 IRB 審查意見，"
  "並實際執行測試或撰寫腳本驗證，最後由開發端抽驗關鍵論點後彙整。")
T(["審查角色", "審查重點"], [
    ["資訊工程／AI", "RAG 檢索品質、紅旗與品質閘的技術可靠度、資安與隱私、評估方法與新穎性"],
    ["腫瘤內科醫師兼臨床研究方法學者", "知識庫對大腸直腸癌化療處方的涵蓋度、急症偵測、臨床試驗設計、SaMD 法規"],
    ["腫瘤護理專家兼護理研究者", "長者與健康識能、自我管理理論、紅旗推播後的護理工作流程、評分面向"],
], [5, 11.5])
P("**限制：這是 AI 模擬的審查，不能取代真人專家。** 審查者引用的臨床論點，本報告只保留經 PubMed 查證過的文獻；"
  "標示「待查證」者需由臨床人員確認。所有修正都應再經實際的腫瘤科醫師、藥師與護理人員審閱。", color=RED)

# ═══════════════════════ 2 審查結論 ═══════════════════════
H("2. 審查結論")
T(["審查者", "系統能否給病人使用", "計畫書能否投稿"], [
    ["資訊工程／AI", "**Reject**", "Major revision"],
    ["腫瘤內科", "**Reject**", "Major revision"],
    ["腫瘤護理", "Major revision（目前不建議用於真實病人）", "Major revision"],
], [5, 6, 5.5])

# ═══════════════════════ 3 重大發現與處理狀態 ═══════════════════════
H("3. 重大發現與處理狀態")
T(["#", "問題", "提出者", "狀態", "負責"], [
    ["1", "紅旗漏接發燒（febrile neutropenia）、出血、脫水、過敏；否定規則誤殺真急症（「沒睡好，今天胸痛」）；"
          "「這樣做有沒有意義」被判自傷", "三位", "已修正（第 4.1 節）", "BOB"],
    ["2", "病人端宣稱「已通知醫療團隊」「護理師會盡快回覆」，實際無人接收；沒有確認（ack）機制；"
          "叫居家病人按呼叫鈴；MEDIUM 不通知任何人", "三位", "已修正（第 4.2 節）", "BOB"],
    ["3", "值班 SOP：誰監看、監看時段、時限、夜間假日處理", "護理、醫師", "部分：草案已完成，需臨床決定", "PI＋護理端"],
    ["4", "知識庫內容與大腸直腸癌常用處方的適配性", "醫師、護理", "已決定：以院內現行單張為準，不修改；範圍外問題由系統轉介", "—"],
    ["5", "正式收案 LLM：預設的 Google AI Studio 免費方案條款允許用輸入資料改善產品", "資工",
     "已決定：Claude Sonnet 5.5 或 Opus 5.5，程式已支援（第 4.4 節）", "BOB"],
    ["6", "品質閘門檻（0.8）擋不住任何範圍外問題；「grounded」標籤名不副實；提示詞寫「優先參考」而非「僅能依據」",
     "資工", "未處理（P1）", "BOB"],
    ["7", "系統只給資訊，沒有支持自我管理（追問試過什麼、事後追蹤、何時就醫提醒）；與 SMSES、MSPSS-C 的機轉連結薄弱",
     "護理", "未處理（需與碩論共同設計）", "同學＋BOB"],
    ["8", "計畫書 v0.1：知識庫字數算錯約 2 倍、ablation 不公平、評估系統與上線系統不一致、缺 non-inferiority margin",
     "資工、醫師", "未處理（P2，計畫書 v0.2）", "BOB"],
    ["9", "試驗設計：版本凍結、依處方分層的測量時點、使用量作為劑量指標、SaMD 判定", "醫師", "需決定", "同學／PI"],
], [0.7, 8.3, 1.8, 3.2, 2.5], status_col=3)

# ═══════════════════════ 4 已完成修正 ═══════════════════════
H("4. 已完成的修正")
H("4.1 紅旗偵測 v2", 2)
B([
    "**發燒**：「發燒／畏寒／發冷／燒燒的／退燒藥」與體溫數值判讀（含全形數字、中文數字），"
    "≥38.0°C 判為 HIGH、37.5–37.9°C 判為 MEDIUM，對齊單張 ONC-22（發燒超過 38°C 通知醫護人員）"
    "與 ONC-25（發燒不可自行吃退燒藥，必須就醫）；依據 ASCO/IDSA 門診發燒與嗜中性白血球低下指引（Taplitz et al., 2018）。",
    "**新增類別**：出血（吐血、黑便、血止不住）、脫水（沒尿、喝水都吐）、過敏反應（臉／嘴唇／喉嚨腫）、"
    "神經症狀、頭部外傷，以及台語說法（喘袂過、想欲死）。",
    "**否定規則改為子句內判斷**：否定詞必須緊貼關鍵字（「沒有胸痛」），假設語境（如果、萬一、預防）只在同一子句生效。",
    "**移除過寬的詞**：「沒有意義」「吞不下」「跌倒了」不再判 HIGH。",
    "**症狀評估對齊 CTCAE v5.0 grade 3**：腹瀉一天 ≥7 次、嘔吐一天 ≥6 次，升級為 HIGH。",
])
T(["測試", "修正前（v1）", "修正後（v2）"], [
    ["資工審查者的急症探針（應判 HIGH，26 句）", "2 / 26", "首次實測 21 / 26；補強後 25 / 26*"],
    ["同一探針（不應判 HIGH，9 句）", "8 / 9 誤判 HIGH", "3 / 9 誤判 HIGH"],
    ["回歸測試集 redflag_cases.json（74 題）", "—", "74 / 74"],
], [7, 3.5, 6])
P("*探針中部分句子已出現在審查報告並被納入回歸集，因此只能算「半獨立」測試；補強後的 25/26 已不是獨立結果。"
  "唯一未判 HIGH 的「一天拉了十幾次水便」會進入症狀評估流程，次數 ≥7 時升級為 HIGH。"
  "剩餘 3 句誤報是刻意的取捨（寧可誤報、不可漏接），例如「我爸以前心悸很嚴重」。", 9.5)
P("**限制：詞庫由開發端整理，尚未經臨床人員審核，也尚未以臨床人員獨立撰寫的測試集驗證。**", color=RED)

H("4.2 通知與確認機制", 2)
B([
    "**據實記錄**：只有在護理端真的有人在線、訊息實際送達時，才記錄「已推播」；沒有人在線時另外留下可稽核的紀錄。",
    "**未確認警示清單**：護理端新增清單，HIGH 排最前面，超過時限標示「逾時」，並顯示「當時無人在線」；"
    "按「已處理」後記錄確認者與時間（只能確認一次）。HIGH 警示有提示音，連線中斷會自動重連。",
    "**MEDIUM 也會推播並進入清單**；知識庫回答不了的問題也進清單（待處理提問），不再石沉大海。",
    "**病人端文字不再承諾做不到的事**（對照見附錄 A）；自傷回覆加入同理語句與 24 小時安心專線 1925。",
    "**值班 SOP 草案**：`docs/ops/警示處理SOP_草案.md`，角色、時段、時限都標為【待確認】。",
])
H("4.3 Claude Sonnet 5.5／Opus 5.5 串接", 2)
B([
    "這兩個模型不接受 temperature 參數（原程式會直接出錯），改用 effort 控制回答深度，預設 low（衛教問答不需深度推理，回應較快）。",
    "模型若基於安全政策拒答，系統改回固定轉介文字，並把問題放進護理端待處理清單，不會顯示空白回覆。",
    "預設開啟 API 的備援機制：拒答時由 API 改用備援模型重跑，資料庫記錄實際回答的模型。"
    "若 IRB 要求只能使用單一模型，可在設定中關閉。【待確認：IRB 文件是否需列出備援模型】",
    "修正審查者發現的小問題：病人當下這則訊息原本會重複送給模型兩次。",
    "目前只用模擬的 API 回應測試，尚未實際呼叫 Claude API（需 API 金鑰與費用）。",
])
H("4.4 測試結果", 2)
P("後端 11 支測試共 367 項全部通過（新增 test_redflag.py 84 項、test_alerts.py 41 項、test_llm_claude.py 30 項），演示引擎 32 項全部通過；"
  "並以瀏覽器實測護理端清單與確認按鈕。GitHub Pages 演示頁改為直接讀取後端匯出的規則與文字，兩邊不會再不一致。")

# ═══════════════════════ 5 尚未處理 ═══════════════════════
H("5. 尚未處理的事項")
T(["優先", "項目", "誰做", "狀態"], [
    ["P0", "Sonnet 5.5 與 Opus 5.5 二擇一：以模擬病人比較正確性、安全性、延遲與成本（需 API 費用）", "BOB", "待評估"],
    ["P0", "確認 Anthropic 資料保存與不用於訓練的條款原文，寫入計畫書與同意書（資料會在境外處理）", "BOB＋同學", "未處理"],
    ["P0", "紅旗詞庫臨床審核＋臨床人員獨立撰寫 held-out 測試集", "臨床人員＋BOB", "未處理"],
    ["P1", "品質閘門檻以資料重新決定；提示詞改為「僅能依據」；研究匯出前清除病人自行輸入的個資", "BOB", "未處理"],
    ["P1", "加入自我管理對話元素（試過什麼、行動計畫、事後追蹤、何時就醫提醒）", "同學＋BOB", "未處理"],
    ["P2", "設計論文計畫書 v0.2（見第 7 節）", "BOB", "未處理"],
], [1.3, 9.2, 3.2, 2.8], status_col=3)

# ═══════════════════════ 6 碩論與 IRB ═══════════════════════
H("6. 與碩士論文及 IRB 的關係")
H("6.1 可用來回覆委員的素材", 2)
T(["審查意見", "現在可以提供的內容", "仍缺"], [
    ["委員一第 1 點：LLM 名稱、版本、資料流向、是否用於訓練",
     "Anthropic Claude Sonnet 5.5 或 Opus 5.5（二擇一）；系統架構；去識別化做法（不送真名或病歷號）",
     "資料在境外處理、保存期限與不用於訓練的條款，需以 Anthropic 當時條款原文確認"],
    ["委員一第 3 點：正確性與安全性驗證、可接受標準、錯誤處理",
     "知識庫來源；紅旗回歸測試集；設計論文的評估方法與預先設定的可接受標準", "臨床人員審核與獨立測試集"],
    ["委員一第 4 點：監控時間、頻率、通知方式、後續流程",
     "警示等級、未確認清單、確認紀錄、逾時標示；SOP 草案", "【待確認：監看人員、時段、時限】"],
    ["委員一第 5 點：AI 系統資料是否為研究資料", "對話、警示、症狀分數皆存於資料庫，身分與研究資料分表",
     "【待決定：是否列為研究資料，並寫入同意書】"],
    ["委員二第 3 點：系統詳細說明", "可由設計論文計畫書第 4 節改寫為附件", "—"],
], [4.5, 7, 5])

H("6.2 試驗設計的建議（供同學參考）", 2)
B([
    "**凍結系統版本**：試驗期間系統若改版，等於中途更換介入措施。建議收案前凍結版本並記錄版本號。",
    "**測量時點依處方分層**：FOLFOX／FOLFIRI 每 14 天一次、CAPOX 每 21 天一次，四次測量的間隔會不同；"
    "建議以療程第幾天錨定，或依處方分層分析（回應委員二第 1 點）。",
    "**記錄使用量作為劑量指標**：實驗組每人使用次數、觸發警示次數，可用於劑量反應分析，也能偵測帳號共用等污染。",
    "**對照組在試驗結束後提供使用機會**（委員二第 4 點），較符合研究倫理。",
    "**自我管理機轉**：系統目前主要提供資訊；若主要結果是症狀自我管理效能（SMSES），需要說明系統如何影響自我效能"
    "（可參考 Symptom Management Model，Dodd et al., 2001）。MSPSS-C（社會支持）目前沒有對應功能，"
    "建議列為探索性指標，或加入照顧者參與功能。",
    "**同意書應揭露**：本系統不是緊急救護服務、不是 24 小時監看；緊急狀況請直接撥打 119 或科室電話。",
    "**SaMD 判定**：症狀評估會依嚴重度決定是否升級，可能被認定為醫療器材軟體，建議向 TFDA 申請判定。【待確認】",
])

# ═══════════════════════ 7 設計論文 ═══════════════════════
H("7. 設計論文方向與計畫書更正")
P("**論文方向已決定採選項 B**：主軸改為「安全層優先的設計與驗證」，紅旗偵測、品質閘與知識庫臨床稽核是主要貢獻；"
  "RAG／CAG／Hybrid 比較降為次要的 ablation。v1 被審查發現的失敗模式與 v2 的修正，可以如實寫成設計研究的迭代過程。")
P("計畫書 v0.1 需要在 v0.2 更正的項目：")
B([
    "**知識庫字數算錯**：v0.1 寫 73,565 字，實際是 35,277 字（扣除參考資料與評值章節約 1.5–1.9 萬字）；"
    "全量載入約 1.5–3 萬 tokens，不是 5.4 萬。Hybrid 的必要性需重新論證。",
    "**比較要公平**：加入等 token 量的 A' 組、各組相同的安全層、parent-document 與 closed-book 基準；評分前統一剝除引文以維持盲化。",
    "**評估的系統必須等於上線系統**：temperature、症狀評估流程都要一致。",
    "**統計**：設定 non-inferiority margin；每組重複執行 ≥3 次；「0 則傷害」需報告 95% 信賴上限。",
    "**評分面向**：加入 actionability（PEMAT；Shoemaker et al., 2014）、同理、是否提醒何時就醫；專家組成加入個管師與病人代表。",
    "**紅旗題組**由臨床人員依急症清單獨立撰寫，避免「只測規則已涵蓋的情境」的循環論證。",
])

# ═══════════════════════ 8 討論題 ═══════════════════════
H("8. 會議討論題")
T(["#", "討論題", "誰決定"], [
    ["1", "Sonnet 5.5 與 Opus 5.5 的比較何時進行？API 費用由誰支付？", "團隊"],
    ["2", "IRB 文件是否需要列出備援模型，或關閉備援機制只用單一模型？", "同學／PI"],
    ["3", "誰負責監看警示？監看時段與確認時限？夜間假日怎麼處理？", "PI＋護理端"],
    ["4", "AI 對話資料是否列為研究資料？同意書如何揭露（含資料在境外處理）？", "同學／PI"],
    ["5", "系統是否加入自我管理對話元素以支持 SMSES？MSPSS-C 如何處理？", "同學＋BOB"],
    ["6", "設計論文的作者排序、preprint 時程，以及碩論的引用方式", "全體"],
    ["7", "紅旗詞庫的臨床審核與獨立測試集由誰撰寫？", "臨床人員"],
], [0.8, 11.2, 4.5])

# ═══════════════════════ 參考文獻 ═══════════════════════
H("參考文獻")
refs = [
    "Ancker, J. S., Edwards, A., Nosal, S., Hauser, D., Mauer, E., Kaushal, R., & the HITEC Investigators. (2017). "
    "Effects of workload, work complexity, and repeated alerts on alert fatigue in a clinical decision support system. "
    "*BMC Medical Informatics and Decision Making, 17*(1), 36. https://doi.org/10.1186/s12911-017-0430-8",
    "Basch, E., Deal, A. M., Kris, M. G., Scher, H. I., Hudis, C. A., Sabbatini, P., Rogak, L., Bennett, A. V., Dueck, A. C., "
    "Atkinson, T. M., Chou, J. F., Dulko, D., Sit, L., Barz, A., Novotny, P., Fruscione, M., Sloan, J. A., & Schrag, D. (2016). "
    "Symptom monitoring with patient-reported outcomes during routine cancer treatment: A randomized controlled trial. "
    "*Journal of Clinical Oncology, 34*(6), 557–565. https://doi.org/10.1200/JCO.2015.63.0830",
    "Dodd, M., Janson, S., Facione, N., Faucett, J., Froelicher, E. S., Humphreys, J., Lee, K., Miaskowski, C., Puntillo, K., "
    "Rankin, S., & Taylor, D. (2001). Advancing the science of symptom management. *Journal of Advanced Nursing, 33*(5), "
    "668–676. https://doi.org/10.1046/j.1365-2648.2001.01697.x",
    "Shoemaker, S. J., Wolf, M. S., & Brach, C. (2014). Development of the Patient Education Materials Assessment Tool "
    "(PEMAT): A new measure of understandability and actionability for print and audiovisual patient information. "
    "*Patient Education and Counseling, 96*(3), 395–403. https://doi.org/10.1016/j.pec.2014.05.027",
    "Taplitz, R. A., Kennedy, E. B., Bow, E. J., Crews, J., Gleason, C., Hawley, D. K., Langston, A. A., Nastoupil, L. J., "
    "Rajotte, M., Rolston, K., Strasfeld, L., & Flowers, C. R. (2018). Outpatient management of fever and neutropenia in "
    "adults treated for malignancy: American Society of Clinical Oncology and Infectious Diseases Society of America "
    "clinical practice guideline update. *Journal of Clinical Oncology, 36*(14), 1443–1453. "
    "https://doi.org/10.1200/JCO.2017.77.6211",
    "U.S. Department of Health and Human Services. (2017). *Common Terminology Criteria for Adverse Events (CTCAE) "
    "version 5.0*. National Cancer Institute.",
]
for r in refs:
    p = rich(doc.add_paragraph(), r, 10)
    p.paragraph_format.left_indent = Cm(1)
    p.paragraph_format.first_line_indent = Cm(-1)

# ═══════════════════════ 附錄 ═══════════════════════
doc.add_page_break()
H("附錄 A：病人端回覆文字修正前後對照")
T(["情境", "修正前（v1）", "修正後（v2）"], [
    ["LINE 紅旗（居家）", "……系統已記錄並通知醫療團隊。",
     "依類別的指引＋聯絡電話＋「這裡不是 24 小時有醫護人員即時監看……請不要等待這裡的回覆」"],
    ["LINE 發燒", "（v1 偵測不到）", "化療期間發燒，可能是嚴重感染的警訊，請不要自行吃退燒藥（衛教單張 ONC-25），需要盡快就醫。"],
    ["自傷", "與一般急症相同的固定文字", "謝謝您願意告訴我……您不是一個人。……請現在就撥打 24 小時安心專線 1925，或撥 119……"],
    ["知識庫外提問（LINE）", "我先幫您記錄下來，護理師會盡快回覆您。",
     "……我沒辦法回答。我會把問題記錄下來供醫療團隊參考，但不保證會有人透過這裡回覆……"],
    ["症狀達門檻（網頁）", "我已經幫您記錄並通知護理師，請稍候……", "我已經幫您記錄並提醒護理站，請按呼叫鈴讓護理師知道。"],
    ["LLM 安全規則（LINE）", "請按旁邊的呼叫鈴", "依管道：LINE 給聯絡電話、網頁才叫按呼叫鈴"],
], [3.5, 5.5, 7.5])

H("附錄 B：紅旗 v2 類別與範例")
T(["類別", "等級", "範例"], [
    ["發燒／感染", "HIGH", "發燒、畏寒、燒燒的、退燒藥、體溫 ≥38.0°C；人工血管紅腫合併發熱"],
    ["心肺", "HIGH", "胸痛、胸口悶、喘不過氣、喘袂過、呼吸有點困難、嘴唇發紫"],
    ["出血", "HIGH", "吐血、咳血、黑便、大便黑黑的、血止不住"],
    ["脫水", "HIGH", "沒尿、喝水都吐、水瀉不止、暈到站不起來"],
    ["過敏反應", "HIGH", "臉／嘴唇／喉嚨腫、喉嚨緊、全身起疹子"],
    ["神經", "HIGH", "昏倒、意識不清、講話不清楚、嘴歪、抽搐、劇烈頭痛"],
    ["外傷", "HIGH", "撞到頭、跌倒撞到頭、骨折"],
    ["自傷", "HIGH（不套否定）", "想死、一了百了、存安眠藥、跳下去、活著沒意思、想欲死"],
    ["注意", "MEDIUM", "體溫 37.5–37.9°C、血便、牙齦或鼻出血、跌倒、吞不下、頭暈、會喘、人工血管紅腫痛"],
], [3, 3, 10.5])
P("完整規則見 `backend/redflag.py`；回歸測試集見 `backend/redflag_cases.json`。", 9.5)

# 頁碼
footer = sec.footer.paragraphs[0]
footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
run = footer.add_run()
for tag, txt in (("begin", None), (None, "PAGE"), ("end", None)):
    el = OxmlElement("w:fldChar" if tag else "w:instrText")
    if tag:
        el.set(qn("w:fldCharType"), tag)
    else:
        el.set(qn("xml:space"), "preserve")
        el.text = txt
    run._r.append(el)

doc.save(OUT)
print("saved", OUT)
