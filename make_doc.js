const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
  Header, Footer, AlignmentType, HeadingLevel, BorderStyle, WidthType,
  ShadingType, VerticalAlign, PageNumber, PageBreak, LevelFormat,
  TableOfContents,
} = require("docx");
const fs = require("fs");

// ── 色彩常數 ──
const TEAL   = "006B5F";
const LTEAL  = "4CB7A7";
const MINT   = "E8F5F3";
const GRAY1  = "F2F4F4";
const GRAY2  = "ECEEEE";
const WHITE  = "FFFFFF";
const RED    = "B71C1C";
const AMBER  = "E65100";
const DARK   = "191C1D";

// ── 共用 border ──
const cellBorder = (color = "C8D8D6") => ({
  style: BorderStyle.SINGLE, size: 1, color,
});
const borders = (color) => ({
  top: cellBorder(color), bottom: cellBorder(color),
  left: cellBorder(color), right: cellBorder(color),
});
const noBorder = { style: BorderStyle.NIL, size: 0, color: "FFFFFF" };
const noBorders = { top: noBorder, bottom: noBorder, left: noBorder, right: noBorder };

// ── cell margins ──
const cm = { top: 100, bottom: 100, left: 150, right: 150 };

// ── 工具函式 ──
function h1(text) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_1,
    spacing: { before: 360, after: 160 },
    children: [new TextRun({ text, font: "Arial", size: 32, bold: true, color: WHITE })],
    shading: { fill: TEAL, type: ShadingType.CLEAR },
    indent: { left: 160, right: 160 },
    border: { bottom: { style: BorderStyle.SINGLE, size: 8, color: LTEAL } },
  });
}

function h2(text) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_2,
    spacing: { before: 280, after: 120 },
    border: { left: { style: BorderStyle.SINGLE, size: 20, color: TEAL } },
    indent: { left: 200 },
    children: [new TextRun({ text, font: "Arial", size: 26, bold: true, color: TEAL })],
  });
}

function h3(text) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_3,
    spacing: { before: 200, after: 80 },
    children: [new TextRun({ text, font: "Arial", size: 24, bold: true, color: "3D4947" })],
  });
}

function body(text, opts = {}) {
  return new Paragraph({
    spacing: { before: 60, after: 80, line: 360 },
    children: [new TextRun({ text, font: "Arial", size: 22, color: DARK, ...opts })],
  });
}

function bullet(text, indent = 720) {
  return new Paragraph({
    numbering: { reference: "bullets", level: 0 },
    spacing: { before: 40, after: 40, line: 340 },
    indent: { left: indent, hanging: 360 },
    children: [new TextRun({ text, font: "Arial", size: 22, color: DARK })],
  });
}

function code(text) {
  return new Paragraph({
    spacing: { before: 40, after: 40 },
    shading: { fill: GRAY1, type: ShadingType.CLEAR },
    indent: { left: 360, right: 360 },
    children: [new TextRun({ text, font: "Courier New", size: 18, color: "1A4731" })],
  });
}

function spacer(n = 1) {
  return Array.from({ length: n }, () => new Paragraph({
    spacing: { before: 0, after: 0 },
    children: [new TextRun("")],
  }));
}

function infoBox(label, text, fillColor = MINT) {
  return new Table({
    width: { size: 9360, type: WidthType.DXA },
    columnWidths: [1400, 7960],
    rows: [new TableRow({
      children: [
        new TableCell({
          borders: borders("4CB7A7"),
          shading: { fill: TEAL, type: ShadingType.CLEAR },
          margins: cm,
          verticalAlign: VerticalAlign.CENTER,
          width: { size: 1400, type: WidthType.DXA },
          children: [new Paragraph({
            alignment: AlignmentType.CENTER,
            children: [new TextRun({ text: label, font: "Arial", size: 20, bold: true, color: WHITE })],
          })],
        }),
        new TableCell({
          borders: borders("4CB7A7"),
          shading: { fill: fillColor, type: ShadingType.CLEAR },
          margins: cm,
          width: { size: 7960, type: WidthType.DXA },
          children: [new Paragraph({
            children: [new TextRun({ text, font: "Arial", size: 21, color: DARK })],
          })],
        }),
      ],
    })],
  });
}

// ── 通用表格 ──
function makeTable(headers, rows, colWidths) {
  const totalW = colWidths.reduce((a, b) => a + b, 0);
  const headerRow = new TableRow({
    tableHeader: true,
    children: headers.map((h, i) => new TableCell({
      borders: borders(LTEAL),
      shading: { fill: TEAL, type: ShadingType.CLEAR },
      margins: cm,
      width: { size: colWidths[i], type: WidthType.DXA },
      children: [new Paragraph({
        alignment: AlignmentType.CENTER,
        children: [new TextRun({ text: h, font: "Arial", size: 20, bold: true, color: WHITE })],
      })],
    })),
  });

  const bodyRows = rows.map((row, ri) => new TableRow({
    children: row.map((cell, ci) => new TableCell({
      borders: borders("C8D8D6"),
      shading: { fill: ri % 2 === 0 ? WHITE : GRAY1, type: ShadingType.CLEAR },
      margins: cm,
      width: { size: colWidths[ci], type: WidthType.DXA },
      children: [new Paragraph({
        children: [new TextRun({ text: cell, font: "Arial", size: 20, color: DARK })],
      })],
    })),
  }));

  return new Table({
    width: { size: totalW, type: WidthType.DXA },
    columnWidths: colWidths,
    rows: [headerRow, ...bodyRows],
  });
}

// ── 主文件 ──
const doc = new Document({
  numbering: {
    config: [{
      reference: "bullets",
      levels: [{
        level: 0, format: LevelFormat.BULLET, text: "\u25AA",
        alignment: AlignmentType.LEFT,
        style: { paragraph: { indent: { left: 720, hanging: 360 } } },
      }],
    }],
  },
  styles: {
    default: { document: { run: { font: "Arial", size: 22 } } },
    paragraphStyles: [
      {
        id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 32, bold: true, font: "Arial", color: WHITE },
        paragraph: { spacing: { before: 360, after: 160 }, outlineLevel: 0 },
      },
      {
        id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 26, bold: true, font: "Arial", color: TEAL },
        paragraph: { spacing: { before: 280, after: 120 }, outlineLevel: 1 },
      },
      {
        id: "Heading3", name: "Heading 3", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 24, bold: true, font: "Arial", color: "3D4947" },
        paragraph: { spacing: { before: 200, after: 80 }, outlineLevel: 2 },
      },
    ],
  },
  sections: [{
    properties: {
      page: {
        size: { width: 11906, height: 16838 },
        margin: { top: 1134, bottom: 1134, left: 1134, right: 1134 },
      },
    },
    headers: {
      default: new Header({
        children: [new Paragraph({
          border: { bottom: { style: BorderStyle.SINGLE, size: 6, color: LTEAL } },
          children: [
            new TextRun({ text: "腫瘤衛教機器人", font: "Arial", size: 18, bold: true, color: TEAL }),
            new TextRun({ text: "  |  系統架構說明書  v1.0", font: "Arial", size: 18, color: "6D7A77" }),
          ],
        })],
      }),
    },
    footers: {
      default: new Footer({
        children: [new Paragraph({
          alignment: AlignmentType.CENTER,
          border: { top: { style: BorderStyle.SINGLE, size: 4, color: LTEAL } },
          children: [
            new TextRun({ text: "第 ", font: "Arial", size: 18, color: "6D7A77" }),
            new TextRun({ children: [PageNumber.CURRENT], font: "Arial", size: 18, color: TEAL, bold: true }),
            new TextRun({ text: " 頁  |  本文件屬研究專案內部資料，請勿外流", font: "Arial", size: 18, color: "6D7A77" }),
          ],
        })],
      }),
    },

    children: [

      // ══════════════ 封面 ══════════════
      ...spacer(4),
      new Paragraph({
        alignment: AlignmentType.CENTER,
        spacing: { before: 0, after: 80 },
        children: [new TextRun({ text: "🏥", size: 96 })],
      }),
      new Paragraph({
        alignment: AlignmentType.CENTER,
        spacing: { before: 0, after: 120 },
        children: [new TextRun({ text: "腫瘤衛教機器人", font: "Arial", size: 56, bold: true, color: TEAL })],
      }),
      new Paragraph({
        alignment: AlignmentType.CENTER,
        spacing: { before: 0, after: 80 },
        children: [new TextRun({ text: "Oncology Patient Education Chatbot", font: "Arial", size: 28, color: LTEAL, italics: true })],
      }),
      new Paragraph({
        alignment: AlignmentType.CENTER,
        spacing: { before: 0, after: 40 },
        children: [new TextRun({ text: "系統架構說明書", font: "Arial", size: 36, bold: true, color: DARK })],
      }),
      new Paragraph({
        alignment: AlignmentType.CENTER,
        spacing: { before: 0, after: 600 },
        children: [new TextRun({ text: "System Architecture Document  v1.0", font: "Arial", size: 22, color: "6D7A77" })],
      }),
      new Table({
        width: { size: 7000, type: WidthType.DXA },
        columnWidths: [2800, 4200],
        rows: [
          ["版本", "1.0.0"],
          ["建立日期", "2026-04-13"],
          ["模型", "Gemma 3 12B (Google AI Studio)"],
          ["向量資料庫", "ChromaDB (本地)"],
          ["嵌入模型", "paraphrase-multilingual-MiniLM-L12-v2"],
          ["後端框架", "FastAPI + Uvicorn"],
          ["知識庫規模", "13 份衛教 PDF / 108 個 chunks"],
        ].map((r, i) => new TableRow({
          children: [
            new TableCell({
              borders: borders(LTEAL),
              shading: { fill: i % 2 === 0 ? TEAL : "005048", type: ShadingType.CLEAR },
              margins: cm, width: { size: 2800, type: WidthType.DXA },
              children: [new Paragraph({ children: [new TextRun({ text: r[0], font: "Arial", size: 20, bold: true, color: WHITE })] })],
            }),
            new TableCell({
              borders: borders(LTEAL),
              shading: { fill: i % 2 === 0 ? MINT : "D4EFEB", type: ShadingType.CLEAR },
              margins: cm, width: { size: 4200, type: WidthType.DXA },
              children: [new Paragraph({ children: [new TextRun({ text: r[1], font: "Arial", size: 20, color: DARK })] })],
            }),
          ],
        })),
      }),
      ...spacer(2),
      new Paragraph({ children: [new PageBreak()] }),

      // ══════════════ 目錄 ══════════════
      new TableOfContents("目  錄", { hyperlink: true, headingStyleRange: "1-3" }),
      new Paragraph({ children: [new PageBreak()] }),

      // ══════════════ 1. 系統概覽 ══════════════
      h1("1. 系統概覽"),
      ...spacer(1),
      body("本系統為一套針對腫瘤化學治療病患設計的 AI 衛教對話機器人，整合本地向量資料庫（ChromaDB）、多語言嵌入模型與大型語言模型（Gemma 3 12B），提供個人化、安全分級的衛教知識問答服務，並具備研究者監控儀表板與即時紅旗警示功能。"),
      ...spacer(1),

      h2("1.1 設計目標"),
      bullet("提供腫瘤化療病患隨時可查詢的衛教知識，降低護理人員的重複性溝通負擔"),
      bullet("依病患年齡與教育程度自動調整語氣（simple / general / detailed）"),
      bullet("偵測緊急關鍵字（如胸痛、呼吸困難、自傷意念），即時推播護理師"),
      bullet("所有回應以本地知識庫為依據，拒絕回答知識庫範圍外的醫療問題"),
      bullet("研究者可即時監控所有病患對話紀錄與紅旗事件"),
      ...spacer(1),

      h2("1.2 系統邊界與限制"),
      ...spacer(1),
      makeTable(
        ["項目", "說明"],
        [
          ["適用對象", "接受化學治療的腫瘤病患（中文使用者）"],
          ["知識範圍", "13 份衛教單張（口腔黏膜炎、噁心嘔吐、便秘、腹瀉、白血球低下等）"],
          ["語言", "繁體中文（介面 + 回應）"],
          ["不支援功能", "診斷病情、藥物劑量建議、英文對話"],
          ["API 配額", "Google AI Studio 免費方案，每日 1,500 次請求"],
          ["回應時間", "平均 25-35 秒（Gemma 3 12B 推理時間）"],
        ],
        [2000, 7360]
      ),
      ...spacer(1),
      new Paragraph({ children: [new PageBreak()] }),

      // ══════════════ 2. 技術架構 ══════════════
      h1("2. 技術架構"),
      ...spacer(1),
      body("系統採三層架構設計："),
      ...spacer(1),

      new Table({
        width: { size: 9360, type: WidthType.DXA },
        columnWidths: [3120, 3120, 3120],
        rows: [
          new TableRow({
            children: [
              new TableCell({
                borders: borders(LTEAL),
                shading: { fill: TEAL, type: ShadingType.CLEAR },
                margins: cm, width: { size: 3120, type: WidthType.DXA },
                children: [
                  new Paragraph({ alignment: AlignmentType.CENTER, children: [new TextRun({ text: "Layer 1", font: "Arial", size: 18, color: "A0D4CE" })] }),
                  new Paragraph({ alignment: AlignmentType.CENTER, children: [new TextRun({ text: "前端呈現層", font: "Arial", size: 22, bold: true, color: WHITE })] }),
                ],
              }),
              new TableCell({
                borders: borders(LTEAL),
                shading: { fill: "005048", type: ShadingType.CLEAR },
                margins: cm, width: { size: 3120, type: WidthType.DXA },
                children: [
                  new Paragraph({ alignment: AlignmentType.CENTER, children: [new TextRun({ text: "Layer 2", font: "Arial", size: 18, color: "A0D4CE" })] }),
                  new Paragraph({ alignment: AlignmentType.CENTER, children: [new TextRun({ text: "後端應用層", font: "Arial", size: 22, bold: true, color: WHITE })] }),
                ],
              }),
              new TableCell({
                borders: borders(LTEAL),
                shading: { fill: "003D36", type: ShadingType.CLEAR },
                margins: cm, width: { size: 3120, type: WidthType.DXA },
                children: [
                  new Paragraph({ alignment: AlignmentType.CENTER, children: [new TextRun({ text: "Layer 3", font: "Arial", size: 18, color: "A0D4CE" })] }),
                  new Paragraph({ alignment: AlignmentType.CENTER, children: [new TextRun({ text: "資料服務層", font: "Arial", size: 22, bold: true, color: WHITE })] }),
                ],
              }),
            ],
          }),
          new TableRow({
            children: [
              new TableCell({
                borders: borders("C8D8D6"),
                shading: { fill: MINT, type: ShadingType.CLEAR },
                margins: cm, width: { size: 3120, type: WidthType.DXA },
                children: [
                  body("HTML5 / CSS3 / Vanilla JS"),
                  body("單頁應用程式（SPA）"),
                  body("WebSocket 即時接收"),
                  body("響應式設計（RWD）"),
                ],
              }),
              new TableCell({
                borders: borders("C8D8D6"),
                shading: { fill: GRAY1, type: ShadingType.CLEAR },
                margins: cm, width: { size: 3120, type: WidthType.DXA },
                children: [
                  body("FastAPI 0.111"),
                  body("Uvicorn ASGI"),
                  body("Gemma 3 12B (google-genai)"),
                  body("alert.py（緊急偵測）"),
                  body("prompt.py（Prompt 引擎）"),
                ],
              }),
              new TableCell({
                borders: borders("C8D8D6"),
                shading: { fill: GRAY2, type: ShadingType.CLEAR },
                margins: cm, width: { size: 3120, type: WidthType.DXA },
                children: [
                  body("ChromaDB（本地向量庫）"),
                  body("sentence-transformers"),
                  body("paraphrase-multilingual"),
                  body("-MiniLM-L12-v2"),
                  body("108 chunks / 13 PDFs"),
                ],
              }),
            ],
          }),
        ],
      }),
      ...spacer(1),

      h2("2.1 資料流程"),
      ...spacer(1),
      infoBox("Step 1", "病患輸入問題 → 前端透過 POST /chat 送出請求"),
      ...spacer(1),
      infoBox("Step 2", "alert.py 掃描輸入：比對 High / Medium 緊急關鍵字清單"),
      ...spacer(1),
      infoBox("Step 3", "RAGRetriever 以 cosine 相似度查詢 ChromaDB，取回最相關的 5 個 chunks"),
      ...spacer(1),
      infoBox("Step 4", "prompt.py 依病患年齡／教育程度組裝 Prompt（simple / general / detailed）"),
      ...spacer(1),
      infoBox("Step 5", "呼叫 Gemma 3 12B API，取得回應文字"),
      ...spacer(1),
      infoBox("Step 6", "若偵測到緊急事件，透過 WebSocket 廣播警報至所有在線研究者"),
      ...spacer(1),
      infoBox("Step 7", "回傳 ChatResponse（含回應、來源、緊急標記）至前端顯示"),
      ...spacer(1),
      new Paragraph({ children: [new PageBreak()] }),

      // ══════════════ 3. 目錄結構 ══════════════
      h1("3. 目錄結構"),
      ...spacer(1),
      body("專案根目錄：C:\\Users\\bobyu\\Desktop\\腫瘤衛教機器人\\"),
      ...spacer(1),
      makeTable(
        ["路徑", "說明"],
        [
          ["docs/", "13 份衛教單張 PDF（ONC-17 ~ ONC-40）"],
          ["chroma_db/", "ChromaDB 本地向量資料庫（自動生成）"],
          ["scripts/indexer.py", "RAG 索引主程式（增量更新）"],
          ["scripts/utils.py", "文字清理、主題推斷工具"],
          ["backend/main.py", "FastAPI 進入點、所有 API 路由"],
          ["backend/config.py", "環境變數設定（pydantic-settings）"],
          ["backend/models.py", "Pydantic 資料模型定義"],
          ["backend/rag.py", "ChromaDB 查詢模組（RAGRetriever）"],
          ["backend/alert.py", "緊急關鍵字分級偵測"],
          ["backend/prompt.py", "個人化 Prompt 組裝引擎"],
          ["backend/websocket_manager.py", "護理師 WebSocket 連線管理"],
          ["frontend/index.html", "前端 SPA（登入 + 病患聊天 + 研究者儀表板）"],
          [".env", "環境變數（Google API Key 等）"],
          ["index_report.json", "索引完成後自動生成的報告"],
        ],
        [3200, 6160]
      ),
      ...spacer(1),
      new Paragraph({ children: [new PageBreak()] }),

      // ══════════════ 4. 各模組說明 ══════════════
      h1("4. 各模組說明"),
      ...spacer(1),

      h2("4.1 RAG 索引系統（scripts/indexer.py）"),
      body("負責將衛教單張轉換為可供語意查詢的向量知識庫。"),
      ...spacer(1),
      makeTable(
        ["參數 / 設定", "值"],
        [
          ["chunk_size", "400 字元"],
          ["chunk_overlap", "80 字元"],
          ["切割分隔符", "\\n\\n、\\n、。、，"],
          ["嵌入模型", "paraphrase-multilingual-MiniLM-L12-v2"],
          ["距離函數", "cosine"],
          ["集合名稱", "patient_education"],
          ["目前 chunks 數", "108"],
          ["更新策略", "增量（MD5 hash 去重）"],
        ],
        [3500, 5860]
      ),
      ...spacer(1),
      body("支援檔案格式：PDF（PyMuPDF）、DOCX（python-docx）"),
      body("疾病分類關鍵字自動對應：腫瘤、心臟、呼吸、泌尿、代謝、骨科等六大類別"),
      ...spacer(1),

      h2("4.2 Prompt 引擎（backend/prompt.py）"),
      body("根據病患 Profile 與 RAG 檢索結果動態組裝系統 Prompt。"),
      ...spacer(1),
      makeTable(
        ["功能", "說明"],
        [
          ["Safety Boundary", "永遠注入最前面：胸痛／呼吸困難等緊急症狀直接要求按呼叫鈴"],
          ["語氣分級", "simple（≤20字/句，比喻說明）/ general（3-5重點）/ detailed（含機制）"],
          ["年齡自動降級", "age >= 65 且 education_level != detailed → 強制切換 simple 語氣"],
          ["RAG 整合", "最多注入 4 段相關衛教內容，每段限 400 字"],
          ["輸出格式", "text / bullet / card_json 三種模式"],
          ["誠實邊界", "知識庫無相關資料時，要求模型明確告知「需請護理師解答」"],
        ],
        [2400, 6960]
      ),
      ...spacer(1),

      h2("4.3 緊急關鍵字偵測（backend/alert.py）"),
      ...spacer(1),
      makeTable(
        ["等級", "範例關鍵字", "處理動作"],
        [
          ["HIGH", "胸痛、呼吸困難、想死、暈倒、跌倒了", "WebSocket 廣播、模型升級（次級）"],
          ["MEDIUM", "很痛、發高燒、吐很多次、藥吃錯了", "標記紀錄、研究者儀表板顯示"],
          ["NONE", "一般衛教問題", "正常流程"],
        ],
        [1500, 5360, 2500]
      ),
      ...spacer(1),

      h2("4.4 語言模型（Gemma 3 12B）"),
      ...spacer(1),
      makeTable(
        ["項目", "說明"],
        [
          ["模型 ID", "gemma-3-12b-it"],
          ["API 提供者", "Google AI Studio (google-genai SDK)"],
          ["system_instruction 支援", "否（Gemma 特性），改以 user/model 對話開頭注入"],
          ["Max Output Tokens", "800"],
          ["Temperature", "0.7"],
          ["對話歷史", "保留最近 12 則（6 輪）"],
          ["免費配額", "每日 1,500 次請求"],
          ["平均回應時間", "25-35 秒"],
        ],
        [3000, 6360]
      ),
      ...spacer(1),

      h2("4.5 前端介面（frontend/index.html）"),
      body("單一 HTML 檔案實作三個畫面的 SPA，無需額外建置工具。"),
      ...spacer(1),
      makeTable(
        ["畫面", "代碼", "主要功能"],
        [
          ["登入頁", "任意", "代碼輸入、後端驗證、角色自動識別"],
          ["病患聊天", "P001", "LINE 風格氣泡、快速 chip、緊急 banner、90s 超時保護"],
          ["研究者儀表板", "R001", "3 欄佈局、病患清單、對話時間軸、Red Flag 面板、WebSocket 即時"],
        ],
        [2200, 1800, 5360]
      ),
      ...spacer(1),
      body("設計系統：Serene Path（Google Stitch 生成），主色 #006B5F，字體 Manrope + Lexend"),
      ...spacer(1),
      new Paragraph({ children: [new PageBreak()] }),

      // ══════════════ 5. API 端點 ══════════════
      h1("5. API 端點說明"),
      ...spacer(1),
      body("Base URL: http://localhost:8000"),
      ...spacer(1),
      makeTable(
        ["方法", "路徑", "說明", "角色"],
        [
          ["GET",  "/",                  "回傳前端 SPA",                 "全部"],
          ["GET",  "/health",            "系統健康檢查（chunks 數、sessions 數）", "全部"],
          ["GET",  "/verify/{code}",     "驗證代碼並回傳角色（patient/researcher）", "全部"],
          ["POST", "/chat",              "RAG 對話，含緊急偵測與 WebSocket 推播", "病患"],
          ["POST", "/patient/{id}",      "建立或更新病患 Profile",        "系統"],
          ["GET",  "/history/{id}",      "取得指定病患的完整對話歷史",    "研究者"],
          ["GET",  "/sessions",          "列出所有 session 摘要與紅旗清單", "研究者"],
          ["WS",   "/ws/nurse",          "護理師即時警報 WebSocket 連線", "研究者"],
        ],
        [900, 2000, 4360, 1400]
      ),
      ...spacer(1),

      h2("5.1 /chat 請求格式"),
      code('POST /chat'),
      code('{'),
      code('  "patient_id": "P001",'),
      code('  "message":    "化療後口腔潰瘍怎麼處理？",'),
      code('  "patient_profile": {              // 選填，若已 /patient 設定可省略'),
      code('    "patient_id": "P001",'),
      code('    "name": "測試病患",'),
      code('    "age": 58,'),
      code('    "diagnosis": ["口腔癌", "化學治療中"],'),
      code('    "education_level": "general"'),
      code('  }'),
      code('}'),
      ...spacer(1),

      h2("5.2 /chat 回應格式"),
      code('{'),
      code('  "response":           "口腔黏膜炎是化療常見副作用...",'),
      code('  "sources":            ["ONC-17化學藥物治療引起的副作用-口腔黏膜炎.pdf"],'),
      code('  "is_emergency":       false,'),
      code('  "emergency_keywords": [],'),
      code('  "session_id":         "P001",'),
      code('  "timestamp":          "2026-04-13T09:32:00"'),
      code('}'),
      ...spacer(1),
      new Paragraph({ children: [new PageBreak()] }),

      // ══════════════ 6. 安全設計 ══════════════
      h1("6. 安全設計"),
      ...spacer(1),

      h2("6.1 緊急安全邊界（Safety Boundary）"),
      body("每次對話的 Prompt 最前方永遠注入以下規則（不可被病患訊息覆蓋）："),
      ...spacer(1),

      new Table({
        width: { size: 9360, type: WidthType.DXA },
        columnWidths: [9360],
        rows: [new TableRow({
          children: [new TableCell({
            borders: { top: cellBorder(RED), bottom: cellBorder(RED), left: { style: BorderStyle.SINGLE, size: 30, color: RED }, right: cellBorder(RED) },
            shading: { fill: "FFF3F3", type: ShadingType.CLEAR },
            margins: { top: 160, bottom: 160, left: 240, right: 240 },
            width: { size: 9360, type: WidthType.DXA },
            children: [
              new Paragraph({ children: [new TextRun({ text: "安全規則（優先於所有指示）", font: "Arial", size: 20, bold: true, color: RED })] }),
              bullet("若病患提到胸痛、呼吸困難、意識喪失、想自傷 → 立即回應「請按呼叫鈴」，停止衛教"),
              bullet("不得提供藥物劑量調整建議"),
              bullet("不得做出診斷"),
              bullet("所有內容僅供參考，建議遵從主治醫師指示"),
            ],
          })],
        })],
      }),
      ...spacer(1),

      h2("6.2 輸入驗證"),
      bullet("登入代碼白名單驗證（VALID_PATIENT_CODES / VALID_RESEARCHER_CODES）"),
      bullet("緊急關鍵字每次請求均掃描，不可跳過"),
      bullet("RAG 相似度過濾：cosine distance > 0.8 的結果不納入回應"),
      ...spacer(1),

      h2("6.3 資料隱私"),
      bullet("所有 session 資料儲存於記憶體（重啟後清空），無持久化個人資料"),
      bullet("ChromaDB 向量庫僅儲存衛教文字與 metadata，不含病患資訊"),
      bullet("Google API 僅傳送對話內容，無識別個人資料"),
      ...spacer(1),
      new Paragraph({ children: [new PageBreak()] }),

      // ══════════════ 7. 測試帳號 ══════════════
      h1("7. 測試帳號"),
      ...spacer(1),
      makeTable(
        ["代碼", "角色", "預載資料", "進入畫面"],
        [
          ["P001", "病患", "58歲、口腔癌化療中、2 輪示範對話、1 個 Red Flag", "手機聊天介面"],
          ["R001", "研究者", "可查看所有 session 紀錄與紅旗警示", "桌面儀表板"],
        ],
        [1400, 1400, 4760, 2000]
      ),
      ...spacer(1),
      body("錯誤代碼輸入時，前端顯示紅色錯誤提示「代號不正確，請重新輸入」。"),
      ...spacer(1),
      new Paragraph({ children: [new PageBreak()] }),

      // ══════════════ 8. 部署說明 ══════════════
      h1("8. 部署說明"),
      ...spacer(1),

      h2("8.1 環境需求"),
      makeTable(
        ["項目", "需求"],
        [
          ["Python", "3.10 以上"],
          ["Node.js", "（僅文件生成需要）"],
          ["作業系統", "Windows 10/11 或 macOS / Linux"],
          ["網路", "需能存取 Google AI Studio API"],
          ["磁碟空間", "約 2 GB（含嵌入模型 ~400 MB）"],
        ],
        [2000, 7360]
      ),
      ...spacer(1),

      h2("8.2 首次啟動步驟"),
      ...spacer(1),
      body("Step 1 — 安裝 Python 套件：", { bold: true }),
      code("pip install fastapi uvicorn[standard] chromadb langchain-text-splitters"),
      code("        pymupdf python-docx sentence-transformers google-genai pydantic-settings"),
      ...spacer(1),
      body("Step 2 — 設定 API Key（編輯 .env）：", { bold: true }),
      code("GOOGLE_API_KEY=<您的 Google AI Studio API Key>"),
      ...spacer(1),
      body("Step 3 — 建立 RAG 知識庫（將 PDF 放入 docs/ 後執行）：", { bold: true }),
      code("python -X utf8 scripts/indexer.py"),
      ...spacer(1),
      body("Step 4 — 啟動後端伺服器：", { bold: true }),
      code("cd backend"),
      code("python -X utf8 -m uvicorn main:app --port 8000 --host 0.0.0.0"),
      ...spacer(1),
      body("Step 5 — 開啟瀏覽器：", { bold: true }),
      code("http://localhost:8000"),
      ...spacer(1),

      h2("8.3 新增衛教單張"),
      bullet("將 PDF 或 DOCX 檔案複製至 docs/ 資料夾"),
      bullet("重新執行 python scripts/indexer.py（已索引的 chunk 自動跳過）"),
      bullet("重啟後端服務使 RAG 生效"),
      ...spacer(1),

      h2("8.4 新增使用者代碼"),
      body("在 backend/main.py 的白名單新增代碼，並於 seed_test_data() 加入對應的 PatientProfile："),
      ...spacer(1),
      code("VALID_PATIENT_CODES    = {'P001', 'P002', ...}  # 新增病患代碼"),
      code("VALID_RESEARCHER_CODES = {'R001', 'R002', ...}  # 新增研究者代碼"),
      ...spacer(1),
      new Paragraph({ children: [new PageBreak()] }),

      // ══════════════ 9. 已知問題與待辦 ══════════════
      h1("9. 已知問題與後續規劃"),
      ...spacer(1),
      makeTable(
        ["項目", "說明", "優先度"],
        [
          ["Gemma 回應時間", "free tier 約 25-35 秒，建議升級付費方案或改用 Gemini Flash", "高"],
          ["Session 持久化", "目前 session 在記憶體，重啟後清空；建議改用 SQLite 或 Redis", "中"],
          ["掃描版 PDF 支援", "圖片型 PDF 需先 OCR（pytesseract）才能索引", "中"],
          ["使用者認證", "目前僅白名單驗證，正式環境應加入 JWT 或 OAuth2", "高"],
          ["多語言支援", "目前僅繁體中文，可擴展至英文或閩南語", "低"],
          ["生產環境部署", "可使用 Docker 容器化、Nginx 反向代理、HTTPS 加密", "中"],
        ],
        [2200, 5360, 1400]
      ),
      ...spacer(1),
      new Paragraph({ children: [new PageBreak()] }),

      // ══════════════ 附錄 ══════════════
      h1("附錄 A — 衛教知識庫清單"),
      ...spacer(1),
      makeTable(
        ["編號", "檔案名稱", "主題", "分類", "Chunks"],
        [
          ["ONC-17", "化學藥物治療引起的副作用-口腔黏膜炎.pdf", "口腔黏膜炎照護", "腫瘤", "9"],
          ["ONC-18", "血小板減少症居家照顧注意事項.pdf",       "血小板低下照護",   "腫瘤", "9"],
          ["ONC-19", "癌症病人紅血球減少之自我照顧.pdf",       "貧血自我照顧",     "呼吸", "9"],
          ["ONC-21", "化學治療引起之副作用-噁心嘔吐食慾不振.pdf","噁心嘔吐",       "腫瘤", "8"],
          ["ONC-22", "化學治療引起之副作用-腹瀉.pdf",          "腹瀉照護",         "泌尿", "7"],
          ["ONC-23", "化學治療引起之副作用-便祕.pdf",          "便秘照護",         "腫瘤", "8"],
          ["ONC-24", "化學治療引起之副作用-毛髮脫落.pdf",      "落髮心理支持",     "腫瘤", "11"],
          ["ONC-25", "白血球低下之自我照顧.pdf",               "感染預防",         "腫瘤", "9"],
          ["ONC-35", "化學治療引起噁心嘔吐之穴位按摩.pdf",     "穴位按摩",         "心臟", "7"],
          ["ONC-37", "化學治療疲憊症狀的自我照護.pdf",         "疲憊管理",         "心臟", "9"],
          ["ONC-38", "化學治療引起的周邊神經病變照顧.pdf",     "神經病變照護",     "腫瘤", "9"],
          ["ONC-39", "化學治療引起的過敏症狀照顧.pdf",         "過敏症狀照護",     "呼吸", "6"],
          ["ONC-40", "化學治療引起的紅疹症狀照顧.pdf",         "皮膚紅疹照護",     "泌尿", "7"],
        ],
        [1200, 4000, 1800, 1200, 1160]
      ),
      ...spacer(2),
      body("合計：13 份文件，108 個 chunks，使用 paraphrase-multilingual-MiniLM-L12-v2 嵌入向量", { color: "6D7A77", italics: true }),
    ],
  }],
});

Packer.toBuffer(doc).then(buf => {
  const outPath = "C:\\Users\\bobyu\\Desktop\\腫瘤衛教機器人\\腫瘤衛教機器人_系統架構書.docx";
  fs.writeFileSync(outPath, buf);
  console.log("DONE:", outPath);
}).catch(e => { console.error("ERROR:", e.message); process.exit(1); });
